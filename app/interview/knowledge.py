"""Loads and validates the YAML knowledge base (questions / rubric / policies)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    text: str
    required: bool = False
    probe_for: tuple[str, ...] = ()
    max_followups: int | None = None
    ask_if: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Question:
        return cls(
            id=d["id"],
            category=d["category"],
            text=" ".join(str(d["text"]).split()),
            required=bool(d.get("required", False)),
            probe_for=tuple(d.get("probe_for", ()) or ()),
            max_followups=d.get("max_followups"),
            ask_if=d.get("ask_if"),
        )


@dataclass(frozen=True)
class RubricCategory:
    key: str
    label: str
    weight: float
    description: str
    anchors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DisqualificationRule:
    id: str
    fact: str
    op: str
    message: str
    value: Any = None
    null_fails: bool = False


@dataclass(frozen=True)
class FactSpec:
    key: str
    type: str
    description: str


@dataclass
class Knowledge:
    questions_raw: dict[str, Any]
    rubric_raw: dict[str, Any]
    policies_raw: dict[str, Any]

    # ---------------------------------------------------------------- rubric
    @property
    def scale_max(self) -> float:
        return float(self.rubric_raw.get("scale", {}).get("max", 10))

    @property
    def categories(self) -> list[RubricCategory]:
        cats = [
            RubricCategory(
                key=c["key"],
                label=c.get("label", c["key"].title()),
                weight=float(c.get("weight", 1.0)),
                description=" ".join(str(c.get("description", "")).split()),
                anchors=dict(c.get("anchors", {}) or {}),
            )
            for c in self.rubric_raw.get("categories", [])
        ]
        if not cats:
            raise ValueError("rubric.yaml defines no categories")
        return cats

    @property
    def normalised_weights(self) -> dict[str, float]:
        cats = self.categories
        total = sum(c.weight for c in cats) or 1.0
        return {c.key: c.weight / total for c in cats}

    @property
    def thresholds(self) -> list[dict[str, Any]]:
        return list(self.rubric_raw.get("recommendation_thresholds", []))

    # -------------------------------------------------------------- policies
    @property
    def company(self) -> dict[str, Any]:
        return dict(self.policies_raw.get("company", {}))

    @property
    def fact_specs(self) -> list[FactSpec]:
        return [
            FactSpec(key=f["key"], type=f.get("type", "string"), description=f.get("description", ""))
            for f in self.policies_raw.get("facts", [])
        ]

    @property
    def disqualification_rules(self) -> list[DisqualificationRule]:
        return [
            DisqualificationRule(
                id=r["id"],
                fact=r["fact"],
                op=r["op"],
                message=r.get("message", f"Failed rule {r['id']}"),
                value=r.get("value"),
                null_fails=bool(r.get("null_fails", False)),
            )
            for r in self.policies_raw.get("disqualification_rules", [])
        ]

    @property
    def early_exit_rule_ids(self) -> set[str]:
        return set(self.policies_raw.get("early_exit_rules", []) or [])

    @property
    def faq(self) -> list[dict[str, str]]:
        return [
            {"q": item["q"], "a": " ".join(str(item["a"]).split())}
            for item in self.policies_raw.get("faq", []) or []
        ]

    @property
    def guardrails(self) -> list[str]:
        return list(self.policies_raw.get("guardrails", []) or [])

    # ------------------------------------------------------------- questions
    def role_block(self, role: str) -> dict[str, Any]:
        roles = self.questions_raw.get("roles", {})
        block = roles.get(role) or roles.get("default")
        if block is None:
            raise ValueError("questions.yaml must define roles.default")
        return block

    def questions_for(self, role: str) -> list[Question]:
        """Resolve a role's question list, applying `inherits` + `extra_questions`."""
        roles = self.questions_raw.get("roles", {})
        block = self.role_block(role)

        parent_key = block.get("inherits")
        if parent_key and parent_key in roles:
            base = [Question.from_dict(q) for q in roles[parent_key].get("questions", [])]
        else:
            base = [Question.from_dict(q) for q in block.get("questions", [])]

        for raw in block.get("extra_questions", []) or []:
            q = Question.from_dict(raw)
            after = raw.get("after")
            idx = next((i for i, b in enumerate(base) if b.id == after), None)
            base.insert(idx + 1 if idx is not None else len(base), q)

        seen: set[str] = set()
        ordered: list[Question] = []
        for q in base:
            if q.id in seen:
                log.warning("duplicate question id %r ignored", q.id)
                continue
            seen.add(q.id)
            ordered.append(q)
        if not ordered:
            raise ValueError(f"no questions resolved for role {role!r}")
        return ordered

    def script_for(self, role: str) -> tuple[str, str]:
        """(opening, closing) templates for a role, falling back to default."""
        block = self.role_block(role)
        default = self.questions_raw.get("roles", {}).get("default", {})
        parent = self.questions_raw.get("roles", {}).get(block.get("inherits"), {})
        opening = block.get("opening") or parent.get("opening") or default.get("opening", "")
        closing = block.get("closing") or parent.get("closing") or default.get("closing", "")
        return " ".join(opening.split()), " ".join(closing.split())

    def category(self, key: str) -> RubricCategory | None:
        return next((c for c in self.categories if c.key == key), None)


def load_knowledge(directory: Path | None = None) -> Knowledge:
    directory = Path(directory or get_settings().knowledge_dir)

    def _read(name: str) -> dict[str, Any]:
        path = directory / name
        if not path.exists():
            raise FileNotFoundError(f"knowledge file missing: {path}")
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    kb = Knowledge(
        questions_raw=_read("questions.yaml"),
        rubric_raw=_read("rubric.yaml"),
        policies_raw=_read("policies.yaml"),
    )

    # Fail fast on the mistakes that are easy to make by hand-editing YAML.
    cat_keys = {c.key for c in kb.categories}
    for role in kb.questions_raw.get("roles", {}):
        for q in kb.questions_for(role):
            if q.category not in cat_keys:
                raise ValueError(
                    f"question {q.id!r} (role {role!r}) has category {q.category!r} "
                    f"which is not in rubric.yaml ({sorted(cat_keys)})"
                )
    fact_keys = {f.key for f in kb.fact_specs}
    for rule in kb.disqualification_rules:
        if rule.fact not in fact_keys:
            raise ValueError(f"disqualification rule {rule.id!r} references unknown fact {rule.fact!r}")
    for rid in kb.early_exit_rule_ids:
        if rid not in {r.id for r in kb.disqualification_rules}:
            raise ValueError(f"early_exit_rules references unknown rule id {rid!r}")
    return kb


@lru_cache
def cached_knowledge() -> Knowledge:
    return load_knowledge()

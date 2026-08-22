"""Loading and validation of the scoring rubric.

The rubric is data, not code: HR edits `config/rubric.yaml` and the change takes
effect on the next run. Everything that could bias or break scoring is validated
here, at load time, rather than discovered mid-batch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class RubricError(ValueError):
    """The rubric file is malformed or internally inconsistent."""


@dataclass(frozen=True)
class Criterion:
    id: str
    name: str
    weight: int
    description: str
    scoring: dict[int, str] = field(default_factory=dict)
    compliance: bool = False


@dataclass(frozen=True)
class Decision:
    pass_threshold: int = 75
    review_threshold: int = 70
    auto_fail_on_compliance: bool = True
    flags_requiring_review: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rubric:
    version: int
    name: str
    decision: Decision
    criteria: tuple[Criterion, ...]
    prohibited_factors: tuple[str, ...] = ()
    source_path: Path | None = None

    def criterion(self, criterion_id: str) -> Criterion:
        for c in self.criteria:
            if c.id == criterion_id:
                return c
        raise KeyError(criterion_id)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(c.id for c in self.criteria)

    def as_yaml(self) -> str:
        """The rubric as the model should see it — no internal bookkeeping."""
        return yaml.safe_dump(
            {
                "criteria": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "weight": c.weight,
                        "description": c.description.strip(),
                        "scoring": {str(k): v for k, v in sorted(c.scoring.items())},
                    }
                    for c in self.criteria
                ]
            },
            sort_keys=False,
            allow_unicode=True,
        )


def load_rubric(path: str | Path) -> Rubric:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RubricError(f"Rubric not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise RubricError(f"Rubric {path} is not valid YAML: {exc}") from exc
    return parse_rubric(raw, source_path=path)


def parse_rubric(raw: object, source_path: Path | None = None) -> Rubric:
    if not isinstance(raw, dict):
        raise RubricError("Rubric must be a YAML mapping.")

    criteria_raw = raw.get("criteria")
    if not isinstance(criteria_raw, list) or not criteria_raw:
        raise RubricError("Rubric must define a non-empty `criteria` list.")

    criteria: list[Criterion] = []
    seen: set[str] = set()
    for index, item in enumerate(criteria_raw):
        if not isinstance(item, dict):
            raise RubricError(f"criteria[{index}] must be a mapping.")
        for required in ("id", "name", "weight", "description"):
            if required not in item:
                raise RubricError(f"criteria[{index}] is missing `{required}`.")
        cid = str(item["id"])
        if cid in seen:
            raise RubricError(f"Duplicate criterion id `{cid}`.")
        seen.add(cid)

        weight = item["weight"]
        if not isinstance(weight, int) or isinstance(weight, bool) or weight <= 0:
            raise RubricError(f"criteria[{cid}].weight must be a positive integer.")

        scoring = item.get("scoring") or {}
        if not isinstance(scoring, dict):
            raise RubricError(f"criteria[{cid}].scoring must be a mapping.")

        criteria.append(
            Criterion(
                id=cid,
                name=str(item["name"]),
                weight=weight,
                description=str(item["description"]),
                scoring={int(k): str(v) for k, v in scoring.items()},
                compliance=bool(item.get("compliance", False)),
            )
        )

    total = sum(c.weight for c in criteria)
    if total != 100:
        raise RubricError(f"Criterion weights must sum to 100, got {total}.")

    decision_raw = raw.get("decision") or {}
    if not isinstance(decision_raw, dict):
        raise RubricError("`decision` must be a mapping.")
    decision = Decision(
        pass_threshold=int(decision_raw.get("pass_threshold", 75)),
        review_threshold=int(decision_raw.get("review_threshold", 70)),
        auto_fail_on_compliance=bool(decision_raw.get("auto_fail_on_compliance", True)),
        flags_requiring_review=tuple(decision_raw.get("flags_requiring_review", ()) or ()),
    )
    for name, value in (
        ("pass_threshold", decision.pass_threshold),
        ("review_threshold", decision.review_threshold),
    ):
        if not 0 <= value <= 100:
            raise RubricError(f"decision.{name} must be between 0 and 100.")

    prohibited = raw.get("prohibited_factors") or ()
    if not isinstance(prohibited, (list, tuple)):
        raise RubricError("`prohibited_factors` must be a list.")

    return Rubric(
        version=int(raw.get("version", 1)),
        name=str(raw.get("name", "Unnamed rubric")),
        decision=decision,
        criteria=tuple(criteria),
        prohibited_factors=tuple(str(p) for p in prohibited),
        source_path=source_path,
    )

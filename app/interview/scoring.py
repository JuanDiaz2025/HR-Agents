"""Deterministic scoring: rubric aggregation, rule evaluation, recommendation.

The LLM supplies per-category scores and extracted facts. Everything that
decides an outcome — weighting, thresholds, disqualification — happens here in
plain Python so results are reproducible and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.interview.knowledge import DisqualificationRule, Knowledge
from app.models import Recommendation


@dataclass
class RuleResult:
    rule_id: str
    failed: bool
    message: str
    skipped: bool = False


@dataclass
class ScoringResult:
    overall_score: float           # 0-100
    recommendation: Recommendation
    category_scores: list[dict[str, Any]]
    disqualified: bool
    disqualification_reasons: list[str]
    failed_rule_ids: list[str]


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_rule(rule: DisqualificationRule, facts: dict[str, Any]) -> RuleResult:
    """A rule states the REQUIREMENT. `failed=True` means the candidate fails it."""
    raw = facts.get(rule.fact)

    if raw is None:
        # Fact never established. Skipping is the safe default: an unanswered
        # question should route to human REVIEW, not an automatic rejection.
        return RuleResult(rule.id, rule.null_fails, rule.message, skipped=not rule.null_fails)

    if rule.op == "is_true":
        return RuleResult(rule.id, not bool(raw), rule.message)
    if rule.op == "is_false":
        return RuleResult(rule.id, bool(raw), rule.message)

    left, right = _as_number(raw), _as_number(rule.value)
    if left is None or right is None:
        # Non-numeric value where a comparison was expected: treat as unknown.
        return RuleResult(rule.id, rule.null_fails, rule.message, skipped=not rule.null_fails)

    ops = {
        "gte": left >= right,
        "lte": left <= right,
        "gt": left > right,
        "lt": left < right,
        "eq": left == right,
        "ne": left != right,
    }
    if rule.op not in ops:
        raise ValueError(f"unknown operator {rule.op!r} in rule {rule.id!r}")
    return RuleResult(rule.id, not ops[rule.op], rule.message)


def evaluate_rules(kb: Knowledge, facts: dict[str, Any]) -> list[RuleResult]:
    return [evaluate_rule(rule, facts) for rule in kb.disqualification_rules]


def compute_overall(kb: Knowledge, raw_scores: dict[str, float]) -> tuple[float, list[dict[str, Any]]]:
    """Weighted average of category scores, expressed out of 100."""
    weights = kb.normalised_weights
    scale_max = kb.scale_max
    rows: list[dict[str, Any]] = []
    weighted_sum = 0.0
    weight_used = 0.0

    for cat in kb.categories:
        score = raw_scores.get(cat.key)
        if score is None:
            continue
        score = max(0.0, min(float(score), scale_max))
        weight = weights[cat.key]
        weighted_sum += score * weight
        weight_used += weight
        rows.append(
            {
                "key": cat.key,
                "label": cat.label,
                "score": round(score, 2),
                "max": scale_max,
                "weight": round(weight, 4),
            }
        )

    if weight_used == 0:
        return 0.0, rows
    # Renormalise over the categories we actually have, so a missing category
    # does not silently drag the score down.
    overall = (weighted_sum / weight_used) / scale_max * 100
    return round(overall, 1), rows


def pick_recommendation(
    kb: Knowledge, overall: float, raw_scores: dict[str, float]
) -> Recommendation:
    for band in kb.thresholds:
        if overall < float(band.get("min_overall", 0)):
            continue
        floors: dict[str, Any] = band.get("min_category", {}) or {}
        if any(
            raw_scores.get(key) is not None and float(raw_scores[key]) < float(floor)
            for key, floor in floors.items()
        ):
            continue
        return Recommendation(band["recommendation"])
    return Recommendation.do_not_proceed


def score_interview(
    kb: Knowledge, raw_scores: dict[str, float], facts: dict[str, Any]
) -> ScoringResult:
    overall, rows = compute_overall(kb, raw_scores)
    results = evaluate_rules(kb, facts)
    failed = [r for r in results if r.failed]

    if failed:
        return ScoringResult(
            overall_score=overall,
            recommendation=Recommendation.do_not_proceed,
            category_scores=rows,
            disqualified=True,
            disqualification_reasons=[r.message for r in failed],
            failed_rule_ids=[r.rule_id for r in failed],
        )

    return ScoringResult(
        overall_score=overall,
        recommendation=pick_recommendation(kb, overall, raw_scores),
        category_scores=rows,
        disqualified=False,
        disqualification_reasons=[],
        failed_rule_ids=[],
    )


def early_exit_reason(kb: Knowledge, facts: dict[str, Any]) -> str | None:
    """A hard blocker confirmed mid-interview, so the AI can wrap up early."""
    early = kb.early_exit_rule_ids
    for result in evaluate_rules(kb, facts):
        if result.failed and result.rule_id in early:
            return result.message
    return None

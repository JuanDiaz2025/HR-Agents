"""Turn criterion scores into a weighted score and a Pass / Not Pass decision.

The model scores criteria; this module applies the bar. Keeping the arithmetic
and the thresholds out of the prompt is what makes two identical submissions
get identical decisions.
"""

from __future__ import annotations

from .models import EvaluationResult, ScoredEvaluation, Verdict
from .rubric import Rubric


class IncompleteEvaluation(ValueError):
    """The model did not score every criterion in the rubric."""


def weighted_score(result: EvaluationResult, rubric: Rubric) -> int:
    """Weighted average of criterion scores, rounded to the nearest integer.

    Raises if any rubric criterion is unscored — a partial evaluation must never
    be silently averaged over the criteria that happen to be present.
    """
    by_id = {c.id: c for c in result.criteria}
    missing = [cid for cid in rubric.ids if cid not in by_id]
    if missing:
        raise IncompleteEvaluation(f"Criteria not scored: {', '.join(missing)}")

    total = sum(by_id[c.id].score * c.weight for c in rubric.criteria)
    return round(total / 100)


def decide(result: EvaluationResult, rubric: Rubric) -> tuple[Verdict, str]:
    """Apply the rubric's decision rules, in precedence order.

    1. A flag requiring review beats everything — a human looks at it.
    2. Low confidence beats the score — we do not auto-decide a guess.
    3. A zeroed compliance criterion is a fail regardless of the total.
    4. Otherwise, the weighted score against the pass threshold.
    """
    d = rubric.decision

    blocking = [f for f in result.flags if f in d.flags_requiring_review]
    if blocking:
        return "NEEDS REVIEW", f"Flagged for human review: {', '.join(blocking)}."

    if result.confidence < d.review_threshold:
        return (
            "NEEDS REVIEW",
            f"Confidence {result.confidence} is below the review threshold "
            f"{d.review_threshold}: {result.confidence_reason}",
        )

    if d.auto_fail_on_compliance:
        by_id = {c.id: c for c in result.criteria}
        for criterion in rubric.criteria:
            if not criterion.compliance:
                continue
            scored = by_id.get(criterion.id)
            if scored is not None and scored.score == 0:
                return (
                    "NOT PASS",
                    f"Compliance criterion '{criterion.name}' scored 0: {scored.rationale}",
                )

    score = weighted_score(result, rubric)
    if score >= d.pass_threshold:
        return "PASS", f"Weighted score {score} meets the pass threshold {d.pass_threshold}."
    return "NOT PASS", f"Weighted score {score} is below the pass threshold {d.pass_threshold}."


def score_evaluation(
    submission_id: str, result: EvaluationResult, rubric: Rubric
) -> ScoredEvaluation:
    unknown = {c.id for c in result.criteria} - set(rubric.ids)
    if unknown:
        # Not fatal: an outdated prompt cache can produce a stale criterion.
        # Drop it rather than fail the row, since the rubric ones are all present.
        result = result.model_copy(
            update={"criteria": [c for c in result.criteria if c.id not in unknown]}
        )

    decision, reason = decide(result, rubric)
    return ScoredEvaluation(
        submission_id=submission_id,
        rubric_version=rubric.version,
        result=result,
        score=weighted_score(result, rubric),
        decision=decision,
        decision_reason=reason,
    )

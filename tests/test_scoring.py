from __future__ import annotations

from app.interview.scoring import (
    compute_overall,
    early_exit_reason,
    evaluate_rules,
    pick_recommendation,
    score_interview,
)
from app.models import Recommendation

PASSING_FACTS = {
    "has_own_computer": True,
    "quiet_workspace": True,
    "has_backup_internet": True,
    "internet_mbps": 50,
    "weekly_hours_available": 40,
    "can_overlap_us_pacific": True,
    "earliest_start_days": 7,
    "expected_monthly_usd": 900,
    "years_relevant_experience": 4,
    "currently_employed_elsewhere_fulltime": False,
}


def test_weighted_overall_uses_rubric_weights(kb):
    # experience carries 0.45, so a low experience score should hurt most.
    strong_exp, _ = compute_overall(kb, {"communication": 4, "experience": 10, "availability": 4})
    weak_exp, _ = compute_overall(kb, {"communication": 10, "experience": 4, "availability": 10})
    assert strong_exp < weak_exp  # 0.55 weight on the two others outweighs 0.45
    assert compute_overall(kb, {"communication": 10, "experience": 10, "availability": 10})[0] == 100.0


def test_missing_category_is_renormalised_not_zeroed(kb):
    overall, rows = compute_overall(kb, {"communication": 8, "experience": 8})
    assert overall == 80.0
    assert {r["key"] for r in rows} == {"communication", "experience"}


def test_scores_are_clamped_to_the_scale(kb):
    overall, rows = compute_overall(kb, {"communication": 99, "experience": -5, "availability": 5})
    assert all(0 <= r["score"] <= kb.scale_max for r in rows)
    assert 0 <= overall <= 100


def test_proceed_requires_category_floors(kb):
    # High overall, but communication below its floor of 6 -> REVIEW, not PROCEED.
    scores = {"communication": 4, "experience": 10, "availability": 10}
    overall, _ = compute_overall(kb, scores)
    assert overall >= 75
    assert pick_recommendation(kb, overall, scores) is Recommendation.review


def test_thresholds_map_to_bands(kb):
    assert pick_recommendation(kb, 90, {"communication": 9, "experience": 9, "availability": 9}) is (
        Recommendation.proceed
    )
    assert pick_recommendation(kb, 60, {"communication": 6, "experience": 6, "availability": 6}) is (
        Recommendation.review
    )
    assert pick_recommendation(kb, 20, {"communication": 2, "experience": 2, "availability": 2}) is (
        Recommendation.do_not_proceed
    )


def test_disqualification_overrides_a_high_score(kb):
    result = score_interview(
        kb, {"communication": 10, "experience": 10, "availability": 10},
        {**PASSING_FACTS, "internet_mbps": 4},
    )
    assert result.overall_score == 100.0
    assert result.recommendation is Recommendation.do_not_proceed
    assert result.disqualified
    assert result.failed_rule_ids == ["slow_internet"]


def test_boundary_value_passes(kb):
    """15 Mbps is the stated minimum, so exactly 15 must pass."""
    result = score_interview(
        kb, {"communication": 8, "experience": 8, "availability": 8},
        {**PASSING_FACTS, "internet_mbps": 15, "weekly_hours_available": 30,
         "expected_monthly_usd": 1500},
    )
    assert not result.disqualified


def test_unknown_facts_do_not_disqualify(kb):
    """An unestablished fact must route to human review, never auto-reject."""
    result = score_interview(kb, {"communication": 8, "experience": 8, "availability": 8}, {})
    assert not result.disqualified
    assert all(r.skipped for r in evaluate_rules(kb, {}))


def test_false_boolean_disqualifies(kb):
    result = score_interview(
        kb, {"communication": 8, "experience": 8, "availability": 8},
        {**PASSING_FACTS, "has_own_computer": False},
    )
    assert result.disqualified
    assert "own computer" in result.disqualification_reasons[0]


def test_multiple_failures_are_all_reported(kb):
    result = score_interview(
        kb, {"communication": 8, "experience": 8, "availability": 8},
        {**PASSING_FACTS, "has_own_computer": False, "quiet_workspace": False},
    )
    assert len(result.disqualification_reasons) == 2


def test_early_exit_only_fires_for_listed_rules(kb):
    assert early_exit_reason(kb, {**PASSING_FACTS, "can_overlap_us_pacific": False})
    # slow_internet is a disqualifier but not in early_exit_rules.
    assert early_exit_reason(kb, {**PASSING_FACTS, "internet_mbps": 2}) is None
    assert early_exit_reason(kb, PASSING_FACTS) is None

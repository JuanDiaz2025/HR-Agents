import pytest

from hr_agents.models import CriterionScore, Evidence
from hr_agents.scoring import IncompleteEvaluation, decide, score_evaluation, weighted_score
from tests.conftest import make_result


def test_weighted_score_is_the_weighted_average(rubric):
    assert weighted_score(make_result(rubric, score=100), rubric) == 100
    assert weighted_score(make_result(rubric, score=60), rubric) == 60


def test_weights_actually_apply(rubric):
    # communication carries 20 of the 100 points; zeroing it costs exactly 20.
    result = make_result(rubric, score=100, scores={"communication": 0})
    assert weighted_score(result, rubric) == 80


def test_missing_criterion_is_an_error_not_a_partial_average(rubric):
    result = make_result(rubric)
    result.criteria.pop()
    with pytest.raises(IncompleteEvaluation):
        weighted_score(result, rubric)


def test_pass_at_the_threshold(rubric):
    result = make_result(rubric, score=rubric.decision.pass_threshold)
    assert decide(result, rubric)[0] == "PASS"


def test_not_pass_just_below_the_threshold(rubric):
    result = make_result(rubric, score=rubric.decision.pass_threshold - 1)
    assert decide(result, rubric)[0] == "NOT PASS"


def test_low_confidence_routes_to_a_human_even_with_a_passing_score(rubric):
    result = make_result(rubric, score=100, confidence=40)
    verdict, reason = decide(result, rubric)
    assert verdict == "NEEDS REVIEW"
    assert "confidence" in reason.lower()


def test_a_blocking_flag_outranks_everything(rubric):
    result = make_result(rubric, score=100, flags=["wrong_person_or_topic"])
    verdict, reason = decide(result, rubric)
    assert verdict == "NEEDS REVIEW"
    assert "wrong_person_or_topic" in reason


def test_a_non_blocking_flag_does_not_derail_a_pass(rubric):
    # missing_required_disclosure is deliberately absent from flags_requiring_review:
    # it is a compliance failure, which the rubric already scores.
    result = make_result(rubric, score=100, flags=["missing_required_disclosure"])
    assert decide(result, rubric)[0] == "PASS"


def test_zeroed_compliance_criterion_fails_regardless_of_total(rubric):
    result = make_result(rubric, score=100, scores={"compliance": 0})
    verdict, reason = decide(result, rubric)
    assert verdict == "NOT PASS"
    assert "compliance" in reason.lower()


def test_zeroing_a_normal_criterion_does_not_auto_fail(rubric):
    result = make_result(rubric, score=100, scores={"professionalism": 0})
    assert decide(result, rubric)[0] == "PASS"


def test_unknown_criteria_are_dropped_not_fatal(rubric):
    result = make_result(rubric, score=80)
    result.criteria.append(
        CriterionScore(
            id="criterion_from_an_older_rubric",
            score=0,
            rationale="stale",
            evidence=[Evidence(timestamp="00:01", observation="x")],
        )
    )
    evaluation = score_evaluation("SUB-1", result, rubric)
    assert evaluation.score == 80
    assert {c.id for c in evaluation.result.criteria} == set(rubric.ids)


def test_score_evaluation_carries_the_rubric_version(rubric):
    evaluation = score_evaluation("SUB-9", make_result(rubric, score=90), rubric)
    assert evaluation.submission_id == "SUB-9"
    assert evaluation.rubric_version == rubric.version
    assert evaluation.decision == "PASS"

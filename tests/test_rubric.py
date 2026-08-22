import pytest

from hr_agents.rubric import RubricError, parse_rubric

BASE = {
    "version": 3,
    "name": "Test",
    "criteria": [
        {"id": "a", "name": "A", "weight": 60, "description": "d"},
        {"id": "b", "name": "B", "weight": 40, "description": "d"},
    ],
}


def test_parses_a_valid_rubric():
    rubric = parse_rubric(BASE)
    assert rubric.version == 3
    assert rubric.ids == ("a", "b")


def test_weights_must_sum_to_100():
    broken = {**BASE, "criteria": [{**BASE["criteria"][0], "weight": 50}, BASE["criteria"][1]]}
    with pytest.raises(RubricError, match="sum to 100"):
        parse_rubric(broken)


def test_duplicate_ids_are_rejected():
    duplicated = {**BASE, "criteria": [BASE["criteria"][0], {**BASE["criteria"][0], "weight": 40}]}
    with pytest.raises(RubricError, match="Duplicate"):
        parse_rubric(duplicated)


def test_missing_field_names_the_field():
    partial = {**BASE, "criteria": [{"id": "a", "name": "A", "weight": 100}]}
    with pytest.raises(RubricError, match="description"):
        parse_rubric(partial)


def test_zero_or_negative_weight_is_rejected():
    zeroed = {**BASE, "criteria": [{**BASE["criteria"][0], "weight": 100}, {**BASE["criteria"][1], "weight": 0}]}
    with pytest.raises(RubricError, match="positive integer"):
        parse_rubric(zeroed)


def test_empty_criteria_is_rejected():
    with pytest.raises(RubricError, match="non-empty"):
        parse_rubric({**BASE, "criteria": []})


def test_thresholds_must_be_percentages():
    with pytest.raises(RubricError, match="pass_threshold"):
        parse_rubric({**BASE, "decision": {"pass_threshold": 140}})


def test_as_yaml_excludes_internal_fields(rubric):
    # The model sees criteria and anchors — not thresholds it could reason backwards from.
    rendered = rubric.as_yaml()
    assert "pass_threshold" not in rendered
    assert "prohibited_factors" not in rendered
    assert "communication" in rendered


def test_shipped_rubric_is_valid(rubric):
    assert sum(c.weight for c in rubric.criteria) == 100
    assert rubric.prohibited_factors
    assert any(c.compliance for c in rubric.criteria)

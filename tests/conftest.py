import pytest

from hr_agents.models import CriterionScore, EvaluationResult, Evidence
from hr_agents.rubric import load_rubric


@pytest.fixture
def rubric():
    return load_rubric("config/rubric.yaml")


def make_result(rubric, score=100, **overrides):
    """An evaluation scoring every rubric criterion at `score`."""
    scores = overrides.pop("scores", {})
    fields = dict(
        criteria=[
            CriterionScore(
                id=c.id,
                score=scores.get(c.id, score),
                rationale="because",
                evidence=[Evidence(timestamp="00:10", observation="said a thing")],
            )
            for c in rubric.criteria
        ],
        confidence=95,
        confidence_reason="clear audio, full response",
        summary="A summary.",
    )
    fields.update(overrides)
    return EvaluationResult(**fields)

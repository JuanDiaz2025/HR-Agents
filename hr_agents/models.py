"""Structured output contract for a single evaluation.

Mirrors `schemas/evaluation_result.json`. The model fills these in; the score
and the Pass / Not Pass decision are computed in `scoring.py`, not here — the
pass bar stays deterministic and auditable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Flag = Literal[
    "unintelligible_audio",
    "video_truncated",
    "wrong_person_or_topic",
    "possible_ai_generated",
    "prohibited_content",
    "missing_required_disclosure",
    "duplicate_submission",
    "technical_error",
]

Verdict = Literal["PASS", "NOT PASS", "NEEDS REVIEW"]


class Evidence(BaseModel):
    timestamp: str = Field(description="Where in the video, as mm:ss or hh:mm:ss.")
    observation: str = Field(description="What was said or done at that point.")


class CriterionScore(BaseModel):
    id: str = Field(description="The criterion id exactly as it appears in the rubric.")
    score: int = Field(ge=0, le=100)
    rationale: str = Field(description="Why this score, in one or two sentences.")
    evidence: list[Evidence] = Field(
        description="Quotes or timestamped observations supporting the score."
    )


class EvaluationResult(BaseModel):
    """What the model returns for one video."""

    criteria: list[CriterionScore]
    confidence: int = Field(
        ge=0,
        le=100,
        description=(
            "Confidence in this scoring. Lower it for poor audio, a partial "
            "recording, or a rubric that does not fit what was submitted."
        ),
    )
    confidence_reason: str = Field(description="Why confidence is at that level.")
    summary: str = Field(description="Two or three sentences an applicant could read.")
    strengths: list[str] = Field(default_factory=list)
    areas_to_improve: list[str] = Field(default_factory=list)
    flags: list[Flag] = Field(
        default_factory=list,
        description="Conditions requiring human attention regardless of score.",
    )


class ScoredEvaluation(BaseModel):
    """The model's evaluation plus the decision computed from it."""

    submission_id: str
    rubric_version: int
    result: EvaluationResult
    score: int
    decision: Verdict
    decision_reason: str

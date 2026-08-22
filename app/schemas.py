"""Request/response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from app.models import InterviewStatus, Recommendation


class BookingRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    scheduled_at: datetime = Field(description="Interview start, with a timezone offset.")
    role: str = "default"
    phone: str | None = None
    source: str | None = Field(default=None, description="e.g. 'google-form', 'calendly'")
    # Used when Google Calendar is not wired up yet, or to bring your own link.
    meeting_url: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class BookingResponse(BaseModel):
    interview_id: str
    candidate_id: str
    status: InterviewStatus
    scheduled_at: datetime
    meeting_url: str | None
    calendar_event_id: str | None
    bot_id: str | None


class CandidateOut(BaseModel):
    id: str
    full_name: str
    email: str
    phone: str | None
    role_applied: str
    source: str | None


class TurnOut(BaseModel):
    sequence: int
    speaker: str
    text: str
    question_id: str | None


class ScorecardOut(BaseModel):
    overall_score: float
    recommendation: Recommendation
    summary: str
    category_scores: list[dict[str, Any]]
    strengths: list[str]
    concerns: list[str]
    facts: dict[str, Any]
    disqualified: bool
    disqualification_reasons: list[str]
    monday_item_id: str | None = None
    sheet_row: int | None = None
    published_at: datetime | None = None


class InterviewOut(BaseModel):
    id: str
    status: InterviewStatus
    scheduled_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    end_reason: str | None
    meeting_url: str | None
    bot_id: str | None
    error: str | None
    candidate: CandidateOut
    scorecard: ScorecardOut | None = None


class TranscriptOut(BaseModel):
    interview_id: str
    candidate: CandidateOut
    turns: list[TurnOut]


class SimulatedUtterance(BaseModel):
    text: str
    speaker_name: str | None = None

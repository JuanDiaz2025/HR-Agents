"""ORM models — the "DATA STORED" box of the architecture."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class InterviewStatus(str, enum.Enum):
    scheduled = "scheduled"
    bot_joining = "bot_joining"
    in_progress = "in_progress"
    completed = "completed"
    no_show = "no_show"
    failed = "failed"
    cancelled = "cancelled"


class Recommendation(str, enum.Enum):
    proceed = "PROCEED"
    review = "REVIEW"
    do_not_proceed = "DO_NOT_PROCEED"


class Speaker(str, enum.Enum):
    interviewer = "interviewer"
    candidate = "candidate"
    system = "system"


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), index=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role_applied: Mapped[str] = mapped_column(String(255), default="general")
    source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    interviews: Mapped[list[Interview]] = relationship(back_populates="candidate")


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), index=True)

    status: Mapped[InterviewStatus] = mapped_column(
        Enum(InterviewStatus), default=InterviewStatus.scheduled, index=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")

    # Google Calendar / Meet
    calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meeting_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Recall.ai
    bot_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    recording_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Engine bookkeeping
    question_plan: Mapped[list] = mapped_column(JSON, default=list)
    plan_cursor: Mapped[int] = mapped_column(Integer, default=0)
    followups_used: Mapped[int] = mapped_column(Integer, default=0)
    questions_asked: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    candidate: Mapped[Candidate] = relationship(back_populates="interviews")
    turns: Mapped[list[TranscriptTurn]] = relationship(
        back_populates="interview", order_by="TranscriptTurn.sequence", cascade="all, delete-orphan"
    )
    scorecard: Mapped[Scorecard | None] = relationship(
        back_populates="interview", uselist=False, cascade="all, delete-orphan"
    )

    @property
    def duration_seconds(self) -> int | None:
        if self.started_at and self.ended_at:
            return int((self.ended_at - self.started_at).total_seconds())
        return None


class TranscriptTurn(Base):
    __tablename__ = "transcript_turns"
    __table_args__ = (UniqueConstraint("interview_id", "sequence", name="uq_turn_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[Speaker] = mapped_column(Enum(Speaker))
    speaker_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    # Question id this turn belongs to, when known.
    question_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    interview: Mapped[Interview] = relationship(back_populates="turns")


class Scorecard(Base):
    __tablename__ = "scorecards"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), unique=True, index=True)

    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    recommendation: Mapped[Recommendation] = mapped_column(Enum(Recommendation))
    summary: Mapped[str] = mapped_column(Text, default="")
    # [{"key","label","score","max","weight","justification"}]
    category_scores: Mapped[list] = mapped_column(JSON, default=list)
    strengths: Mapped[list] = mapped_column(JSON, default=list)
    concerns: Mapped[list] = mapped_column(JSON, default=list)
    # Extracted structured facts used by the deterministic rules.
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    disqualified: Mapped[bool] = mapped_column(Boolean, default=False)
    disqualification_reasons: Mapped[list] = mapped_column(JSON, default=list)

    # Downstream publication state
    sheet_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monday_item_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    interview: Mapped[Interview] = relationship(back_populates="scorecard")


class WebhookEvent(Base):
    """Idempotency ledger — Recall retries deliveries for up to 24h."""

    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # webhook-id header
    event: Mapped[str | None] = mapped_column(String(128), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

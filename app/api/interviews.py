from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import runtime as get_runtime
from app.db import get_session
from app.models import Interview, InterviewStatus, Speaker
from app.runtime import Runtime
from app.schemas import (
    CandidateOut,
    InterviewOut,
    ScorecardOut,
    SimulatedUtterance,
    TranscriptOut,
    TurnOut,
)
from app.security import require_internal_key

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/interviews",
    tags=["interviews"],
    dependencies=[Depends(require_internal_key)],
)


def _to_out(interview: Interview) -> InterviewOut:
    sc = interview.scorecard
    return InterviewOut(
        id=interview.id,
        status=interview.status,
        scheduled_at=interview.scheduled_at,
        started_at=interview.started_at,
        ended_at=interview.ended_at,
        duration_seconds=interview.duration_seconds,
        end_reason=interview.end_reason,
        meeting_url=interview.meeting_url,
        bot_id=interview.bot_id,
        error=interview.error,
        candidate=CandidateOut(
            id=interview.candidate.id,
            full_name=interview.candidate.full_name,
            email=interview.candidate.email,
            phone=interview.candidate.phone,
            role_applied=interview.candidate.role_applied,
            source=interview.candidate.source,
        ),
        scorecard=None
        if sc is None
        else ScorecardOut(
            overall_score=sc.overall_score,
            recommendation=sc.recommendation,
            summary=sc.summary,
            category_scores=sc.category_scores,
            strengths=sc.strengths,
            concerns=sc.concerns,
            facts=sc.facts,
            disqualified=sc.disqualified,
            disqualification_reasons=sc.disqualification_reasons,
            monday_item_id=sc.monday_item_id,
            sheet_row=sc.sheet_row,
            published_at=sc.published_at,
        ),
    )


@router.get("", response_model=list[InterviewOut])
def list_interviews(
    session: Session = Depends(get_session),
    status_filter: InterviewStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, le=500),
) -> list[InterviewOut]:
    stmt = select(Interview).order_by(Interview.scheduled_at.desc()).limit(limit)
    if status_filter is not None:
        stmt = stmt.where(Interview.status == status_filter)
    return [_to_out(i) for i in session.scalars(stmt).all()]


@router.get("/{interview_id}", response_model=InterviewOut)
def get_interview(interview_id: str, session: Session = Depends(get_session)) -> InterviewOut:
    interview = session.get(Interview, interview_id)
    if interview is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown interview")
    return _to_out(interview)


@router.get("/{interview_id}/transcript", response_model=TranscriptOut)
def get_transcript(interview_id: str, session: Session = Depends(get_session)) -> TranscriptOut:
    interview = session.get(Interview, interview_id)
    if interview is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown interview")
    return TranscriptOut(
        interview_id=interview.id,
        candidate=CandidateOut(
            id=interview.candidate.id,
            full_name=interview.candidate.full_name,
            email=interview.candidate.email,
            phone=interview.candidate.phone,
            role_applied=interview.candidate.role_applied,
            source=interview.candidate.source,
        ),
        turns=[
            TurnOut(
                sequence=t.sequence,
                speaker=t.speaker.value,
                text=t.text,
                question_id=t.question_id,
            )
            for t in interview.turns
        ],
    )


@router.get("/{interview_id}/transcript.txt", response_class=PlainTextResponse)
def get_transcript_text(interview_id: str, session: Session = Depends(get_session)) -> str:
    interview = session.get(Interview, interview_id)
    if interview is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown interview")
    lines = [
        f"Interview {interview.id}",
        f"Candidate: {interview.candidate.full_name} <{interview.candidate.email}>",
        f"Role: {interview.candidate.role_applied}",
        f"Scheduled: {interview.scheduled_at.isoformat()}",
        f"Status: {interview.status.value}",
        "",
    ]
    for turn in interview.turns:
        who = {
            Speaker.interviewer: "INTERVIEWER",
            Speaker.candidate: interview.candidate.full_name.upper(),
            Speaker.system: "SYSTEM",
        }[turn.speaker]
        lines.append(f"{who}: {turn.text}")
    return "\n".join(lines)


@router.post("/{interview_id}/rescore", response_model=InterviewOut)
async def rescore(interview_id: str, rt: Runtime = Depends(get_runtime)) -> InterviewOut:
    """Re-run scoring and re-publish. Useful after editing the rubric."""
    await rt.pipeline.run(interview_id)
    from app.db import session_scope

    with session_scope() as session:
        interview = session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown interview")
        return _to_out(interview)


# ---------------------------------------------------------------------------
# Test harness endpoints — drive the engine without a real meeting.
# ---------------------------------------------------------------------------
@router.post("/{interview_id}/simulate/start", status_code=status.HTTP_202_ACCEPTED)
async def simulate_start(interview_id: str, rt: Runtime = Depends(get_runtime)) -> dict[str, str]:
    await rt.engine.start(interview_id)
    return {"status": "started"}


@router.post("/{interview_id}/simulate/say", status_code=status.HTTP_202_ACCEPTED)
async def simulate_say(
    interview_id: str, payload: SimulatedUtterance, rt: Runtime = Depends(get_runtime)
) -> dict[str, str]:
    """Feed one candidate utterance in, then advance immediately rather than
    waiting out the silence debounce."""
    await rt.engine.on_utterance(
        interview_id, text=payload.text, speaker_name=payload.speaker_name or "Candidate"
    )
    await rt.engine.advance(interview_id)
    return {"status": "ok"}

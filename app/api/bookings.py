from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import runtime as get_runtime
from app.runtime import Runtime
from app.schemas import BookingRequest, BookingResponse
from app.security import require_internal_key
from app.services.scheduling import SchedulingError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bookings", tags=["bookings"], dependencies=[Depends(require_internal_key)])


@router.post("", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
async def create_booking(
    payload: BookingRequest, rt: Runtime = Depends(get_runtime)
) -> BookingResponse:
    """Book an interview: creates the Calendar event with a Meet link and
    schedules the Recall.ai bot to join it.

    This is the endpoint your Google Form / Calendly / Zapier webhook calls.
    """
    try:
        interview = await rt.scheduling.book(
            full_name=payload.full_name,
            email=str(payload.email),
            scheduled_at=payload.scheduled_at,
            role=payload.role,
            phone=payload.phone,
            source=payload.source,
            meeting_url=payload.meeting_url,
            extra=payload.extra,
        )
    except SchedulingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return BookingResponse(
        interview_id=interview.id,
        candidate_id=interview.candidate_id,
        status=interview.status,
        scheduled_at=interview.scheduled_at,
        meeting_url=interview.meeting_url,
        calendar_event_id=interview.calendar_event_id,
        bot_id=interview.bot_id,
    )


@router.delete("/{interview_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_booking(interview_id: str, rt: Runtime = Depends(get_runtime)) -> None:
    try:
        await rt.scheduling.cancel(interview_id)
    except SchedulingError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

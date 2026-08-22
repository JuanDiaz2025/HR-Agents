"""Booking flow — boxes 1 through 3.

    booking request -> candidate record -> Calendar event + Meet link
                    -> scheduled Recall bot
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.clients.google_calendar import CalendarClient
from app.clients.recall import RecallClient
from app.config import Settings, get_settings
from app.db import session_scope
from app.models import Candidate, Interview, InterviewStatus

log = logging.getLogger(__name__)

# Recall guarantees on-time joins only for bots scheduled more than 10 minutes
# out; inside that window we get an ad-hoc bot from a finite warm pool.
SCHEDULED_BOT_MIN_LEAD = timedelta(minutes=11)


class SchedulingError(RuntimeError):
    pass


class SchedulingService:
    def __init__(
        self,
        *,
        calendar: CalendarClient,
        recall: RecallClient,
        settings: Settings | None = None,
    ):
        self.calendar = calendar
        self.recall = recall
        self.settings = settings or get_settings()

    async def book(
        self,
        *,
        full_name: str,
        email: str,
        scheduled_at: datetime,
        role: str = "default",
        phone: str | None = None,
        source: str | None = None,
        meeting_url: str | None = None,
        extra: dict | None = None,
        create_bot: bool = True,
    ) -> Interview:
        if scheduled_at.tzinfo is None:
            raise SchedulingError("scheduled_at must include a timezone offset")
        now = datetime.now(UTC)
        if scheduled_at <= now:
            raise SchedulingError("scheduled_at is in the past")

        # ---------------------------------------------------- candidate record
        with session_scope() as session:
            candidate = session.scalars(
                select(Candidate).where(Candidate.email == email.lower()).limit(1)
            ).first()
            if candidate is None:
                candidate = Candidate(
                    full_name=full_name,
                    email=email.lower(),
                    phone=phone,
                    role_applied=role,
                    source=source,
                    extra=extra or {},
                )
                session.add(candidate)
            else:
                candidate.full_name = full_name or candidate.full_name
                candidate.phone = phone or candidate.phone
                candidate.role_applied = role or candidate.role_applied
            session.flush()
            interview = Interview(
                candidate_id=candidate.id,
                scheduled_at=scheduled_at,
                timezone=self.settings.interview_timezone,
                status=InterviewStatus.scheduled,
            )
            session.add(interview)
            session.flush()
            interview_id = interview.id
            candidate_name = candidate.full_name

        # ------------------------------------------------- calendar + Meet link
        try:
            event = self.calendar.create_interview_event(
                candidate_name=candidate_name,
                candidate_email=email,
                role=role,
                start=scheduled_at,
                meeting_url=meeting_url,
            )
        except Exception as exc:
            self._mark_failed(interview_id, f"calendar: {exc}")
            raise SchedulingError(f"could not create the calendar event: {exc}") from exc

        with session_scope() as session:
            interview = session.get(Interview, interview_id)
            assert interview is not None
            interview.calendar_event_id = event.event_id
            interview.meeting_url = event.meeting_url

        # ------------------------------------------------------- schedule bot
        if not create_bot:
            log.info("[%s] booked without a bot (create_bot=False)", interview_id[:8])
            return self._load(interview_id)

        join_at = scheduled_at if scheduled_at - now > SCHEDULED_BOT_MIN_LEAD else None
        if join_at is None:
            log.warning(
                "[%s] interview is less than 11 minutes out — creating an ad-hoc bot, "
                "which can fail with 507 if the warm pool is empty",
                interview_id[:8],
            )
        try:
            bot = await self.recall.create_bot(
                meeting_url=event.meeting_url,
                interview_id=interview_id,
                join_at=join_at,
                candidate_name=candidate_name,
            )
        except Exception as exc:
            self._mark_failed(interview_id, f"recall: {exc}")
            raise SchedulingError(f"could not schedule the interviewer bot: {exc}") from exc

        with session_scope() as session:
            interview = session.get(Interview, interview_id)
            assert interview is not None
            interview.bot_id = bot.get("id")
            recordings = bot.get("recordings") or []
            if recordings:
                interview.recording_id = recordings[0].get("id")

        log.info(
            "[%s] booked %s at %s (bot %s)",
            interview_id[:8],
            candidate_name,
            scheduled_at.isoformat(),
            bot.get("id"),
        )
        return self._load(interview_id)

    async def cancel(self, interview_id: str) -> None:
        with session_scope() as session:
            interview = session.get(Interview, interview_id)
            if interview is None:
                raise SchedulingError(f"unknown interview {interview_id}")
            bot_id, event_id = interview.bot_id, interview.calendar_event_id
            interview.status = InterviewStatus.cancelled

        if bot_id:
            try:
                await self.recall.delete_scheduled_bot(bot_id)
            except Exception:
                log.exception("[%s] could not delete bot %s", interview_id[:8], bot_id)
        if event_id:
            try:
                self.calendar.cancel_event(event_id)
            except Exception:
                log.exception("[%s] could not cancel event %s", interview_id[:8], event_id)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _mark_failed(interview_id: str, error: str) -> None:
        with session_scope() as session:
            interview = session.get(Interview, interview_id)
            if interview is not None:
                interview.status = InterviewStatus.failed
                interview.error = error[:2000]

    @staticmethod
    def _load(interview_id: str) -> Interview:
        with session_scope() as session:
            interview = session.get(Interview, interview_id)
            assert interview is not None
            # Touch the relationship so it is loaded before the session closes.
            _ = interview.candidate.full_name
            return interview

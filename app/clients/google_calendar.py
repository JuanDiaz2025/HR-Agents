"""Google Calendar (boxes 2 & 3) — create the event, generate the Meet link,
invite the AI interviewer account."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.clients.google_auth import GoogleNotConfigured, calendar_service
from app.config import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass
class ScheduledEvent:
    event_id: str
    meeting_url: str
    html_link: str | None = None


class CalendarClient(Protocol):
    def create_interview_event(self, **kwargs) -> ScheduledEvent: ...
    def cancel_event(self, event_id: str) -> None: ...


class GoogleCalendarClient:
    def __init__(self, settings: Settings | None = None, service=None):
        self.settings = settings or get_settings()
        self._service = service or calendar_service(self.settings)

    def create_interview_event(
        self,
        *,
        candidate_name: str,
        candidate_email: str,
        role: str,
        start: datetime,
        duration_minutes: int | None = None,
        description_extra: str = "",
        meeting_url: str | None = None,
    ) -> ScheduledEvent:
        """Create the event. Google generates a Meet link unless `meeting_url`
        is supplied, in which case that link is used as-is."""
        s = self.settings
        duration = duration_minutes or s.interview_duration_minutes
        end = start + timedelta(minutes=duration)

        body = {
            "summary": f"AI Screening Interview — {candidate_name} ({role})",
            "description": (
                f"Automated screening interview for {candidate_name}.\n"
                f"Role: {role}\n\n"
                "An AI interviewer will join this call and conduct a short "
                "screening conversation. The call is recorded and transcribed.\n"
                f"{description_extra}"
            ).strip(),
            "start": {"dateTime": start.isoformat(), "timeZone": s.interview_timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": s.interview_timezone},
            "attendees": [
                {"email": candidate_email, "responseStatus": "needsAction"},
                # Inviting the AI interviewer mailbox is what makes the bot's
                # calendar-driven join possible and keeps the audit trail clean.
                {"email": s.interviewer_email, "responseStatus": "accepted"},
            ],
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "email", "minutes": 24 * 60},
                    {"method": "email", "minutes": 30},
                ],
            },
        }

        if meeting_url:
            body["location"] = meeting_url
            body["description"] = f"{body['description']}\n\nJoin: {meeting_url}"
        else:
            body["conferenceData"] = {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }

        event = (
            self._service.events()
            .insert(
                calendarId=s.calendar_id,
                body=body,
                conferenceDataVersion=0 if meeting_url else 1,
                sendUpdates="all",
            )
            .execute()
        )

        meeting_url = (
            meeting_url or event.get("hangoutLink") or self._extract_meet_url(event)
        )
        if not meeting_url:
            raise RuntimeError(
                f"Calendar event {event.get('id')} was created without a Meet link. "
                "Check that Google Meet is enabled for the impersonated user."
            )
        log.info("created calendar event %s with meet link", event.get("id"))
        return ScheduledEvent(
            event_id=event["id"], meeting_url=meeting_url, html_link=event.get("htmlLink")
        )

    @staticmethod
    def _extract_meet_url(event: dict) -> str | None:
        for entry in (event.get("conferenceData", {}) or {}).get("entryPoints", []) or []:
            if entry.get("entryPointType") == "video" and entry.get("uri"):
                return entry["uri"]
        return None

    def cancel_event(self, event_id: str) -> None:
        self._service.events().delete(
            calendarId=self.settings.calendar_id, eventId=event_id, sendUpdates="all"
        ).execute()


class ManualCalendarClient:
    """Fallback when Google is not wired up: the caller supplies the Meet link
    themselves (paste it into the booking request). Keeps the whole pipeline
    usable on day one, before Workspace delegation is approved."""

    def create_interview_event(
        self,
        *,
        candidate_name: str,
        candidate_email: str,
        role: str,
        start: datetime,
        duration_minutes: int | None = None,
        description_extra: str = "",
        meeting_url: str | None = None,
    ) -> ScheduledEvent:
        if not meeting_url:
            raise GoogleNotConfigured(
                "Google Calendar is not configured, so a meeting_url must be "
                "supplied with the booking request."
            )
        return ScheduledEvent(event_id=f"manual-{uuid.uuid4()}", meeting_url=meeting_url)

    def cancel_event(self, event_id: str) -> None:
        log.info("manual calendar: nothing to cancel for %s", event_id)


def build_calendar_client(settings: Settings | None = None) -> CalendarClient:
    settings = settings or get_settings()
    try:
        return GoogleCalendarClient(settings)
    except GoogleNotConfigured as exc:
        log.warning("%s — falling back to manual Meet links", exc)
        return ManualCalendarClient()
    except Exception:
        log.exception("could not build Google Calendar client — falling back to manual links")
        return ManualCalendarClient()

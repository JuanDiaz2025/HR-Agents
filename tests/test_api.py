from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.clients.google_calendar import ScheduledEvent
from app.db import SessionLocal
from app.interview.voice import CollectingVoice
from app.main import create_app
from app.models import Interview, InterviewStatus
from app.security import _expected_signature
from tests.conftest import FakeBrain

WEBHOOK_SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
AUTH = {"X-API-Key": "test-key"}


class FakeCalendar:
    def __init__(self):
        self.created: list[dict] = []
        self.cancelled: list[str] = []

    def create_interview_event(self, **kwargs):
        self.created.append(kwargs)
        return ScheduledEvent(
            event_id="evt-1", meeting_url="https://meet.google.com/fake-link-xyz"
        )

    def cancel_event(self, event_id):
        self.cancelled.append(event_id)


class FakeRecall:
    def __init__(self):
        self.created: list[dict] = []
        self.deleted: list[str] = []

    async def create_bot(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "bot-777", "recordings": [{"id": "rec-777"}]}

    async def delete_scheduled_bot(self, bot_id):
        self.deleted.append(bot_id)

    async def aclose(self):
        return None


@pytest.fixture
def client(kb):
    """App wired to fakes, with signature verification switched on."""
    app = create_app()
    with TestClient(app) as test_client:
        rt = app.state.runtime
        rt.settings.recall_webhook_secret = WEBHOOK_SECRET
        rt.calendar = FakeCalendar()
        rt.recall = FakeRecall()
        rt.scheduling.calendar = rt.calendar
        rt.scheduling.recall = rt.recall
        rt.brain = FakeBrain()
        rt.engine.brain = rt.brain
        rt.pipeline.brain = rt.brain
        rt.voice = CollectingVoice()
        rt.engine.voice = rt.voice
        yield test_client


def signed(body: dict, msg_id: str = "msg-1") -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    timestamp = str(int(time.time()))
    return raw, {
        "webhook-id": msg_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": f"v1,{_expected_signature(WEBHOOK_SECRET, msg_id, timestamp, raw)}",
        "content-type": "application/json",
    }


# ------------------------------------------------------------------ auth gates
def test_endpoints_require_the_internal_key(client):
    assert client.get("/api/interviews").status_code == 401
    assert client.post("/api/bookings", json={}).status_code == 401


def test_health_is_open(client):
    assert client.get("/health").json() == {"status": "ok"}


# -------------------------------------------------------------------- bookings
def test_booking_creates_event_and_schedules_bot(client):
    when = datetime.now(UTC) + timedelta(days=1)
    response = client.post(
        "/api/bookings",
        headers=AUTH,
        json={
            "full_name": "Ana Santos",
            "email": "ana@example.com",
            "scheduled_at": when.isoformat(),
            "role": "virtual_assistant",
            "phone": "+639170000000",
            "source": "google-form",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["meeting_url"] == "https://meet.google.com/fake-link-xyz"
    assert body["bot_id"] == "bot-777"
    assert body["status"] == "scheduled"

    rt = client.app.state.runtime
    # More than 11 minutes out, so it must be a *scheduled* bot.
    assert rt.recall.created[0]["join_at"] is not None
    assert rt.calendar.created[0]["candidate_email"] == "ana@example.com"


def test_imminent_booking_creates_an_adhoc_bot(client):
    when = datetime.now(UTC) + timedelta(minutes=5)
    response = client.post(
        "/api/bookings",
        headers=AUTH,
        json={"full_name": "Rush Job", "email": "rush@example.com", "scheduled_at": when.isoformat()},
    )
    assert response.status_code == 201
    assert client.app.state.runtime.recall.created[0]["join_at"] is None


def test_booking_in_the_past_is_rejected(client):
    when = datetime.now(UTC) - timedelta(hours=1)
    response = client.post(
        "/api/bookings",
        headers=AUTH,
        json={"full_name": "X", "email": "x@example.com", "scheduled_at": when.isoformat()},
    )
    assert response.status_code == 400
    assert "past" in response.json()["detail"]


def test_naive_datetime_is_rejected(client):
    response = client.post(
        "/api/bookings",
        headers=AUTH,
        json={
            "full_name": "X",
            "email": "x@example.com",
            "scheduled_at": (datetime.now() + timedelta(days=1)).isoformat(),  # noqa: DTZ005
        },
    )
    assert response.status_code == 400
    assert "timezone" in response.json()["detail"]


def test_rebooking_reuses_the_candidate_record(client):
    when = datetime.now(UTC) + timedelta(days=1)
    payload = {
        "full_name": "Ana Santos",
        "email": "ana@example.com",
        "scheduled_at": when.isoformat(),
    }
    first = client.post("/api/bookings", headers=AUTH, json=payload).json()
    payload["scheduled_at"] = (when + timedelta(days=1)).isoformat()
    second = client.post("/api/bookings", headers=AUTH, json=payload).json()
    assert first["candidate_id"] == second["candidate_id"]
    assert first["interview_id"] != second["interview_id"]


def test_cancel_removes_bot_and_event(client):
    when = datetime.now(UTC) + timedelta(days=1)
    booking = client.post(
        "/api/bookings",
        headers=AUTH,
        json={"full_name": "Ana", "email": "ana@example.com", "scheduled_at": when.isoformat()},
    ).json()
    assert client.delete(f"/api/bookings/{booking['interview_id']}", headers=AUTH).status_code == 204
    rt = client.app.state.runtime
    assert rt.recall.deleted == ["bot-777"]
    assert rt.calendar.cancelled == ["evt-1"]


# ---------------------------------------------------------------- bot webhooks
def test_unsigned_status_webhook_is_rejected(client):
    response = client.post(
        "/api/webhooks/recall/status", json={"event": "bot.done", "data": {"bot": {"id": "bot-777"}}}
    )
    assert response.status_code == 401


def test_status_webhook_starts_the_interview(client, make_interview):
    iv = make_interview(bot_id="bot-777")
    raw, headers = signed(
        {
            "event": "bot.in_call_recording",
            "data": {"data": {"code": "in_call_recording"}, "bot": {"id": "bot-777"}},
        }
    )
    response = client.post("/api/webhooks/recall/status", content=raw, headers=headers)
    assert response.status_code == 200 and response.json()["status"] == "accepted"

    with SessionLocal() as session:
        assert session.get(Interview, iv).status is InterviewStatus.in_progress
    assert client.app.state.runtime.voice.utterances  # greeted the candidate


def test_status_webhook_is_deduplicated(client, make_interview):
    make_interview(bot_id="bot-777")
    body = {"event": "bot.in_call_recording", "data": {"data": {}, "bot": {"id": "bot-777"}}}
    raw, headers = signed(body, msg_id="dupe-1")
    assert client.post("/api/webhooks/recall/status", content=raw, headers=headers).json()["status"] == "accepted"
    assert client.post("/api/webhooks/recall/status", content=raw, headers=headers).json()["status"] == "duplicate"
    assert len(client.app.state.runtime.voice.utterances) == 1


def test_interview_id_in_metadata_is_preferred_over_bot_lookup(client, make_interview):
    iv = make_interview(bot_id=None)
    raw, headers = signed(
        {
            "event": "bot.in_call_recording",
            "data": {"data": {}, "bot": {"id": "unknown-bot", "metadata": {"interview_id": iv}}},
        }
    )
    assert client.post("/api/webhooks/recall/status", content=raw, headers=headers).json()["status"] == "accepted"
    with SessionLocal() as session:
        assert session.get(Interview, iv).status is InterviewStatus.in_progress


def test_unmatched_bot_is_reported_not_crashed(client):
    raw, headers = signed({"event": "bot.done", "data": {"bot": {"id": "ghost"}}})
    response = client.post("/api/webhooks/recall/status", content=raw, headers=headers)
    assert response.status_code == 200 and response.json()["status"] == "unmatched"


def test_fatal_event_marks_the_interview_failed(client, make_interview):
    iv = make_interview(bot_id="bot-777")
    raw, headers = signed(
        {
            "event": "bot.fatal",
            "data": {
                "data": {"code": "fatal", "sub_code": "meeting_link_invalid"},
                "bot": {"id": "bot-777"},
            },
        }
    )
    client.post("/api/webhooks/recall/status", content=raw, headers=headers)
    with SessionLocal() as session:
        interview = session.get(Interview, iv)
        assert interview.status is InterviewStatus.failed
        assert "meeting_link_invalid" in interview.error


def test_done_event_finishes_and_scores(client, make_interview):
    iv = make_interview(bot_id="bot-777")
    start_raw, start_headers = signed(
        {"event": "bot.in_call_recording", "data": {"data": {}, "bot": {"id": "bot-777"}}},
        msg_id="s-1",
    )
    client.post("/api/webhooks/recall/status", content=start_raw, headers=start_headers)

    utterance = {
        "event": "transcript.data",
        "data": {
            "data": {
                "words": [{"text": "I"}, {"text": "have"}, {"text": "four"}, {"text": "years"},
                          {"text": "of"}, {"text": "admin"}, {"text": "experience"}],
                "participant": {"id": 2, "name": "Ana Santos"},
            },
            "bot": {"id": "bot-777"},
        },
    }
    assert client.post(
        "/api/webhooks/recall/realtime/",
        params={"token": "test-realtime-token"},
        json=utterance,
    ).json()["status"] == "transcript"

    done_raw, done_headers = signed(
        {
            "event": "bot.done",
            "data": {"data": {"sub_code": "call_ended"}, "bot": {"id": "bot-777"}},
        },
        msg_id="d-1",
    )
    client.post("/api/webhooks/recall/status", content=done_raw, headers=done_headers)

    with SessionLocal() as session:
        interview = session.get(Interview, iv)
        assert interview.status is InterviewStatus.completed
        assert interview.scorecard is not None
        assert interview.scorecard.recommendation.value == "PROCEED"


# ------------------------------------------------------------ realtime webhook
def test_realtime_requires_the_token(client):
    response = client.post("/api/webhooks/recall/realtime/", json={"event": "transcript.data"})
    assert response.status_code == 401
    response = client.post(
        "/api/webhooks/recall/realtime/", params={"token": "nope"}, json={"event": "x"}
    )
    assert response.status_code == 401


def test_realtime_participant_events_are_accepted(client, make_interview):
    make_interview(bot_id="bot-777")
    for event, expected in [("participant_events.join", "join"), ("participant_events.leave", "leave")]:
        response = client.post(
            "/api/webhooks/recall/realtime/",
            params={"token": "test-realtime-token"},
            json={
                "event": event,
                "data": {"data": {"participant": {"name": "Ana"}}, "bot": {"id": "bot-777"}},
            },
        )
        assert response.json()["status"] == expected


def test_empty_transcript_is_ignored(client, make_interview):
    make_interview(bot_id="bot-777")
    response = client.post(
        "/api/webhooks/recall/realtime/",
        params={"token": "test-realtime-token"},
        json={"event": "transcript.data", "data": {"data": {"words": []}, "bot": {"id": "bot-777"}}},
    )
    assert response.json()["status"] == "empty"


# ------------------------------------------------------------------ read paths
def test_interview_read_endpoints(client, make_interview):
    iv = make_interview()
    client.post(f"/api/interviews/{iv}/simulate/start", headers=AUTH)
    client.post(
        f"/api/interviews/{iv}/simulate/say",
        headers=AUTH,
        json={"text": "I have four years of admin experience."},
    )

    detail = client.get(f"/api/interviews/{iv}", headers=AUTH).json()
    assert detail["status"] == "in_progress"
    assert detail["candidate"]["full_name"] == "Ana Santos"

    transcript = client.get(f"/api/interviews/{iv}/transcript", headers=AUTH).json()
    assert any(t["speaker"] == "candidate" for t in transcript["turns"])

    text = client.get(f"/api/interviews/{iv}/transcript.txt", headers=AUTH).text
    assert "ANA SANTOS:" in text

    listing = client.get("/api/interviews", headers=AUTH, params={"status": "in_progress"}).json()
    assert [i["id"] for i in listing] == [iv]


def test_unknown_interview_is_404(client):
    assert client.get("/api/interviews/nope", headers=AUTH).status_code == 404
    assert client.get("/api/interviews/nope/transcript", headers=AUTH).status_code == 404


# ------------------------------------------------------------------ agent page
def test_agent_page_requires_the_token(client):
    assert client.get("/agent/iv-1").status_code == 401
    response = client.get("/agent/iv-1", params={"token": "test-realtime-token"})
    assert response.status_code == 200
    assert "SpeechSynthesisUtterance" in response.text
    assert "iv-1" in response.text


def test_unconfigured_recall_logs_speech_instead_of_crashing(make_interview):
    """An unconfigured deployment must not 500 on its own greeting — every other
    integration degrades to a logged no-op and speech has to as well."""
    from app.interview.voice import LoggingVoice

    app = create_app()
    with TestClient(app) as client:
        rt = app.state.runtime
        assert not rt.settings.recall_api_key
        assert isinstance(rt.voice, LoggingVoice)
        rt.brain = FakeBrain()
        rt.engine.brain = rt.brain

        iv = make_interview()
        assert client.post(f"/api/interviews/{iv}/simulate/start", headers=AUTH).status_code == 202
        assert rt.voice.utterances

        health = client.get("/health/integrations").json()
        assert health["checks"]["voice_output"]["live"] is False

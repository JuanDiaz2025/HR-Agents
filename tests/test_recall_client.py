from __future__ import annotations

import base64
from datetime import UTC

import httpx
import pytest

from app.clients.recall import SILENT_MP3_B64, RecallClient, RecallError
from app.config import Settings


def client_with(transport: httpx.MockTransport, **overrides) -> RecallClient:
    settings = Settings(recall_api_key="test-recall-key", **overrides)
    http = httpx.AsyncClient(
        transport=transport,
        base_url=settings.recall_base_url,
        headers={"Authorization": f"Token {settings.recall_api_key}"},
    )
    return RecallClient(settings, client=http)


def test_silent_mp3_placeholder_is_valid_base64():
    audio = base64.b64decode(SILENT_MP3_B64)
    assert audio.startswith(b"ID3")  # a real mp3 frame, not filler text


def test_payload_schedules_a_bot_and_requests_realtime_transcripts():
    payload = client_with(httpx.MockTransport(lambda r: httpx.Response(200))).build_bot_payload(
        meeting_url="https://meet.google.com/abc-defg-hij", interview_id="iv-1"
    )
    transcript = payload["recording_config"]["transcript"]
    assert "recallai_streaming" in transcript["provider"]
    endpoint = payload["recording_config"]["realtime_endpoints"][0]
    assert endpoint["type"] == "webhook"
    assert "transcript.data" in endpoint["events"]
    # The Output Audio endpoint is gated on automatic_audio_output being set
    # at create time, so it must always be present in output_audio mode.
    assert payload["automatic_audio_output"]["in_call_recording"]["data"]["kind"] == "mp3"
    assert payload["metadata"]["interview_id"] == "iv-1"
    assert "join_at" not in payload


def test_websocket_transport_switches_the_endpoint():
    recall = client_with(
        httpx.MockTransport(lambda r: httpx.Response(200)),
        recall_realtime_transport="websocket",
        public_base_url="https://interviewer.example.com",
    )
    endpoint = recall.build_bot_payload(meeting_url="u", interview_id="iv")[
        "recording_config"
    ]["realtime_endpoints"][0]
    assert endpoint["type"] == "websocket"
    assert endpoint["url"].startswith("wss://")


def test_output_media_mode_points_the_bot_at_our_agent_page():
    recall = client_with(
        httpx.MockTransport(lambda r: httpx.Response(200)),
        voice_output_mode="output_media",
        public_base_url="https://interviewer.example.com",
    )
    payload = recall.build_bot_payload(meeting_url="u", interview_id="iv-9")
    url = payload["output_media"]["camera"]["config"]["url"]
    assert url.startswith("https://interviewer.example.com/agent/iv-9")
    assert "automatic_audio_output" not in payload


def test_google_meet_waiting_room_timeout_respects_the_platform_cap():
    payload = client_with(httpx.MockTransport(lambda r: httpx.Response(200))).build_bot_payload(
        meeting_url="u", interview_id="iv"
    )
    # Google Meet rejects anything above 600.
    assert payload["automatic_leave"]["waiting_room_timeout"] <= 600


async def test_join_at_is_sent_for_scheduled_bots():
    from datetime import datetime, timedelta

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(201, json={"id": "bot-abc", "recordings": [{"id": "rec-1"}]})

    recall = client_with(httpx.MockTransport(handler))
    join_at = datetime.now(UTC) + timedelta(hours=2)
    bot = await recall.create_bot(meeting_url="u", interview_id="iv-1", join_at=join_at)
    assert bot["id"] == "bot-abc"
    assert seen["join_at"] == join_at.isoformat()


async def test_507_is_retried_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(507, text="ad-hoc pool exhausted")
        return httpx.Response(201, json={"id": "bot-xyz"})

    recall = client_with(httpx.MockTransport(handler))
    bot = await recall.create_bot(
        meeting_url="u", interview_id="iv", max_507_retries=5, retry_delay_s=0
    )
    assert bot["id"] == "bot-xyz"
    assert calls["n"] == 3


async def test_507_gives_up_after_the_retry_budget():
    recall = client_with(httpx.MockTransport(lambda r: httpx.Response(507, text="nope")))
    with pytest.raises(RecallError) as exc:
        await recall.create_bot(
            meeting_url="u", interview_id="iv", max_507_retries=2, retry_delay_s=0
        )
    assert exc.value.status_code == 507


async def test_non_507_errors_are_not_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad meeting url")

    recall = client_with(httpx.MockTransport(handler))
    with pytest.raises(RecallError):
        await recall.create_bot(meeting_url="nonsense", interview_id="iv", retry_delay_s=0)
    assert calls["n"] == 1


async def test_output_audio_base64_encodes_the_mp3():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        assert request.url.path.endswith("/bot/bot-1/output_audio/")
        return httpx.Response(200, json={})

    recall = client_with(httpx.MockTransport(handler))
    await recall.output_audio("bot-1", b"\xff\xfbfake-mp3")
    assert seen["kind"] == "mp3"
    assert base64.b64decode(seen["b64_data"]) == b"\xff\xfbfake-mp3"

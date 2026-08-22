"""Recall.ai client (box "B") — schedules the bot, and makes it speak.

Docs: https://docs.recall.ai/reference/bot_create
Auth: the API key goes in the `Authorization` header (the `Token ` prefix is
optional). Region is part of the hostname.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime
from typing import Any

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

# 0.2 s of silent mp3. Recall requires `automatic_audio_output` to be present at
# bot-create time before the Output Audio endpoint may be used, so we register
# this and never actually let anyone hear it.
SILENT_MP3_B64 = (
    "SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAAAAAAAAAAAAAA//tQxAADB"
    "AAAAAAAAAAAAAAAAAAAAABJbmZvAAAADwAAAAMAAAGwAFVVVVVVVVVVVVVVVVVVVVVVVV"
    "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
    "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
    "VQ=="
)

# Real-time events we subscribe to. `transcript.data` carries the finalised
# utterances the engine reacts to; the participant events let us detect the
# candidate joining and leaving.
REALTIME_EVENTS = [
    "transcript.data",
    "participant_events.join",
    "participant_events.leave",
]


class RecallError(RuntimeError):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"Recall.ai returned {status_code}: {body[:500]}")
        self.status_code = status_code
        self.body = body


class RecallClient:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

    # ------------------------------------------------------------------ plumbing
    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            if not self.settings.recall_api_key:
                raise RuntimeError("RECALL_API_KEY is not configured")
            self._client = httpx.AsyncClient(
                base_url=self.settings.recall_base_url,
                headers={
                    "Authorization": f"Token {self.settings.recall_api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        client = await self._http()
        response = await client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise RecallError(response.status_code, response.text)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    # -------------------------------------------------------------- bot payload
    def build_bot_payload(
        self,
        *,
        meeting_url: str,
        interview_id: str,
        join_at: datetime | None = None,
        candidate_name: str | None = None,
    ) -> dict[str, Any]:
        s = self.settings

        if s.recall_realtime_transport == "websocket":
            realtime_endpoint = {
                "type": "websocket",
                "url": s.realtime_websocket_url,
                "events": REALTIME_EVENTS,
            }
        else:
            realtime_endpoint = {
                "type": "webhook",
                "url": s.realtime_webhook_url,
                "events": REALTIME_EVENTS,
            }

        payload: dict[str, Any] = {
            "meeting_url": meeting_url,
            "bot_name": s.bot_name,
            # Metadata values must be strings.
            "metadata": {
                "interview_id": interview_id,
                "candidate_name": candidate_name or "",
                "app": "hr-agents",
            },
            "recording_config": {
                "transcript": {
                    "provider": {
                        "recallai_streaming": {
                            "mode": "prioritize_low_latency",
                            "language_code": "en",
                        }
                    },
                    "diarization": {"use_separate_streams_when_available": True},
                },
                "realtime_endpoints": [realtime_endpoint],
            },
            "automatic_leave": {
                # Google Meet caps the waiting-room timeout at 600 s.
                "waiting_room_timeout": 600,
                "noone_joined_timeout": 600,
                "everyone_left_timeout": {"timeout": 30},
                "in_call_recording_timeout": s.max_interview_seconds + 300,
                "silence_detection": {
                    "timeout": s.bot_silence_timeout_s,
                    "activate_after": 60,
                },
            },
        }

        if s.voice_output_mode == "output_audio":
            # Required to unlock the Output Audio endpoint.
            payload["automatic_audio_output"] = {
                "in_call_recording": {"data": {"kind": "mp3", "b64_data": SILENT_MP3_B64}}
            }
        else:
            payload["output_media"] = {
                "camera": {
                    "kind": "webpage",
                    "config": {
                        "url": (
                            f"{s.public_base_url}/agent/{interview_id}"
                            f"?token={s.recall_realtime_token}"
                        )
                    },
                }
            }

        if join_at is not None:
            payload["join_at"] = join_at.isoformat()
        return payload

    # -------------------------------------------------------------------- bots
    async def create_bot(
        self,
        *,
        meeting_url: str,
        interview_id: str,
        join_at: datetime | None = None,
        candidate_name: str | None = None,
        max_507_retries: int = 10,
        retry_delay_s: float = 30.0,
    ) -> dict[str, Any]:
        """Create (or schedule) a bot.

        A `join_at` more than 10 minutes out creates a *scheduled* bot, which
        Recall guarantees will join on time. Without it we get an ad-hoc bot
        from a warm pool that can be exhausted — hence the documented 507
        retry loop.
        """
        payload = self.build_bot_payload(
            meeting_url=meeting_url,
            interview_id=interview_id,
            join_at=join_at,
            candidate_name=candidate_name,
        )
        attempt = 0
        while True:
            try:
                bot = await self._request("POST", "/bot/", json=payload)
                log.info("created recall bot %s for interview %s", bot.get("id"), interview_id)
                return bot
            except RecallError as exc:
                if exc.status_code != 507 or attempt >= max_507_retries:
                    raise
                attempt += 1
                log.warning(
                    "recall ad-hoc bot pool exhausted (507), retry %d/%d in %.0fs",
                    attempt,
                    max_507_retries,
                    retry_delay_s,
                )
                await asyncio.sleep(retry_delay_s)

    async def get_bot(self, bot_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/bot/{bot_id}/")

    async def update_scheduled_bot(self, bot_id: str, **fields: Any) -> dict[str, Any]:
        """Only works before the bot starts joining, and `join_at` can only be
        changed while it is more than 10 minutes away."""
        return await self._request("PATCH", f"/bot/{bot_id}/", json=fields)

    async def delete_scheduled_bot(self, bot_id: str) -> None:
        await self._request("DELETE", f"/bot/{bot_id}/")

    async def leave_call(self, bot_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/bot/{bot_id}/leave_call/")

    # ------------------------------------------------------------------- speech
    async def output_audio(self, bot_id: str, mp3_bytes: bytes) -> dict[str, Any]:
        """Play an mp3 into the meeting. Requires `automatic_audio_output`."""
        return await self._request(
            "POST",
            f"/bot/{bot_id}/output_audio/",
            json={"kind": "mp3", "b64_data": base64.b64encode(mp3_bytes).decode("ascii")},
        )

    async def stop_output_audio(self, bot_id: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/bot/{bot_id}/output_audio/")

    async def start_output_media(self, bot_id: str, webpage_url: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/bot/{bot_id}/output_media/",
            json={"camera": {"kind": "webpage", "config": {"url": webpage_url}}},
        )

    async def stop_output_media(self, bot_id: str) -> dict[str, Any]:
        return await self._request(
            "DELETE", f"/bot/{bot_id}/output_media/", json={"camera": True}
        )

    async def send_chat_message(self, bot_id: str, text: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"/bot/{bot_id}/send_chat_message/", json={"message": text}
        )

    # ---------------------------------------------------------------- artifacts
    async def get_recording(self, recording_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/recording/{recording_id}/")

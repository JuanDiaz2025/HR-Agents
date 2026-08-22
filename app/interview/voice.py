"""Voice output — how the interviewer's words reach the meeting.

The engine only ever calls `speak(text)`. Which of these implementations is
behind it is a configuration choice, and swapping one in is how you test the
engine without touching a real meeting.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from app.clients.recall import RecallClient
from app.clients.tts import TTSProvider

log = logging.getLogger(__name__)


class VoiceOutput(Protocol):
    async def speak(self, bot_id: str | None, text: str) -> None: ...


class OutputAudioVoice:
    """TTS on the server, then POST the mp3 to Recall's Output Audio endpoint.

    Simplest reliable path for a turn-taking interview: no browser, no
    WebRTC. The trade-off is latency — one TTS round trip plus one Recall
    round trip before the candidate hears anything.
    """

    def __init__(self, recall: RecallClient, tts: TTSProvider):
        self.recall = recall
        self.tts = tts

    async def speak(self, bot_id: str | None, text: str) -> None:
        if not text.strip():
            return
        if not bot_id:
            log.warning("no bot_id — dropping speech: %s", text)
            return
        audio = await self.tts.synthesize(text)
        await self.recall.output_audio(bot_id, audio)


class BrowserAgentVoice:
    """For `voice_output_mode=output_media`: the bot streams a webpage we host,
    and that page speaks. We hand the text to whichever browser page is
    connected for this interview over our own WebSocket."""

    def __init__(self) -> None:
        # interview_id -> asyncio.Queue of pending utterances
        self._queues: dict[str, asyncio.Queue[str]] = {}

    def queue_for(self, interview_id: str) -> asyncio.Queue[str]:
        return self._queues.setdefault(interview_id, asyncio.Queue())

    def release(self, interview_id: str) -> None:
        self._queues.pop(interview_id, None)

    async def speak(self, bot_id: str | None, text: str) -> None:
        raise NotImplementedError("use speak_to_interview for the browser agent")

    async def speak_to_interview(self, interview_id: str, text: str) -> None:
        if text.strip():
            await self.queue_for(interview_id).put(text)


class CollectingVoice:
    """Records what would have been said. Used by tests and the simulator."""

    def __init__(self) -> None:
        self.utterances: list[str] = []

    async def speak(self, bot_id: str | None, text: str) -> None:
        if text.strip():
            self.utterances.append(text)


class LoggingVoice(CollectingVoice):
    """Used when Recall.ai is not configured. Every other integration degrades
    to a logged no-op, and speech has to as well — otherwise the first thing an
    unconfigured deployment does is 500 on its own greeting."""

    async def speak(self, bot_id: str | None, text: str) -> None:
        await super().speak(bot_id, text)
        if text.strip():
            log.info("[no recall] would speak to bot %s: %s", bot_id or "-", text)

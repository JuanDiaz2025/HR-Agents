"""Service container. Built once at startup and attached to `app.state`.

Wiring lives here so that swapping a real integration for a fake is a
one-line change in a test rather than a monkeypatch of module globals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.clients.google_calendar import CalendarClient, build_calendar_client
from app.clients.llm import Brain, build_brain
from app.clients.monday import MondaySink, build_monday_sink
from app.clients.notify import Notifier
from app.clients.recall import RecallClient
from app.clients.sheets import SheetsSink, build_sheets_sink
from app.clients.tts import TTSProvider, build_tts
from app.config import Settings, get_settings
from app.interview.engine import InterviewEngine
from app.interview.knowledge import Knowledge, cached_knowledge
from app.interview.voice import (
    BrowserAgentVoice,
    LoggingVoice,
    OutputAudioVoice,
    VoiceOutput,
)
from app.services.pipeline import ResultsPipeline
from app.services.scheduling import SchedulingService

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    settings: Settings
    knowledge: Knowledge
    brain: Brain
    recall: RecallClient
    tts: TTSProvider
    voice: VoiceOutput
    engine: InterviewEngine
    pipeline: ResultsPipeline
    scheduling: SchedulingService
    notifier: Notifier
    sheets: SheetsSink
    monday: MondaySink
    calendar: CalendarClient
    browser_voice: BrowserAgentVoice | None = None

    async def aclose(self) -> None:
        await self.recall.aclose()
        await self.tts.aclose()
        await self.monday.aclose()
        await self.notifier.aclose()


def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or get_settings()
    knowledge = cached_knowledge()

    brain = build_brain(settings)
    recall = RecallClient(settings)
    tts = build_tts(settings)
    notifier = Notifier(settings)
    sheets = build_sheets_sink(settings)
    monday = build_monday_sink(settings)
    calendar = build_calendar_client(settings)

    browser_voice: BrowserAgentVoice | None = None
    voice: VoiceOutput
    if not settings.recall_api_key:
        log.warning(
            "RECALL_API_KEY is not configured — the interviewer will log what it "
            "would say instead of speaking into a meeting."
        )
        voice = LoggingVoice()
    elif settings.voice_output_mode == "output_media":
        browser_voice = BrowserAgentVoice()
        voice = _BrowserVoiceBridge(browser_voice)
    else:
        voice = OutputAudioVoice(recall, tts)

    pipeline = ResultsPipeline(
        brain=brain,
        sheets=sheets,
        monday=monday,
        notifier=notifier,
        knowledge=knowledge,
        settings=settings,
    )

    engine = InterviewEngine(
        brain=brain,
        voice=voice,
        knowledge=knowledge,
        settings=settings,
        on_finished=pipeline.run,
    )
    scheduling = SchedulingService(calendar=calendar, recall=recall, settings=settings)

    return Runtime(
        settings=settings,
        knowledge=knowledge,
        brain=brain,
        recall=recall,
        tts=tts,
        voice=voice,
        engine=engine,
        pipeline=pipeline,
        scheduling=scheduling,
        notifier=notifier,
        sheets=sheets,
        monday=monday,
        calendar=calendar,
        browser_voice=browser_voice,
    )


class _BrowserVoiceBridge:
    """Adapts the engine's `speak(bot_id, text)` call onto the per-interview
    queue the agent webpage drains. The engine passes a bot id, but the queue is
    keyed by interview id, so we resolve one from the other."""

    def __init__(self, browser_voice: BrowserAgentVoice):
        self.browser_voice = browser_voice

    async def speak(self, bot_id: str | None, text: str) -> None:
        from sqlalchemy import select

        from app.db import session_scope
        from app.models import Interview

        if not bot_id:
            log.warning("browser voice: no bot id, dropping speech")
            return
        with session_scope() as session:
            interview_id = session.scalar(
                select(Interview.id).where(Interview.bot_id == bot_id).limit(1)
            )
        if not interview_id:
            log.warning("browser voice: no interview for bot %s", bot_id)
            return
        await self.browser_voice.speak_to_interview(interview_id, text)

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import runtime as get_runtime
from app.clients.google_calendar import ManualCalendarClient
from app.clients.llm import ScriptedBrain
from app.clients.monday import NullMondaySink
from app.clients.sheets import NullSheetsSink
from app.clients.tts import SilentTTS
from app.interview.voice import LoggingVoice
from app.runtime import Runtime

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/integrations")
def integrations(rt: Runtime = Depends(get_runtime)) -> dict[str, object]:
    """Which integrations are live vs. running in fallback mode.

    Use this after deploying: anything reported as a fallback will silently do
    nothing in production.
    """
    s = rt.settings
    checks = {
        "claude": {
            "live": not isinstance(rt.brain, ScriptedBrain),
            "detail": "scripted brain — set ANTHROPIC_API_KEY"
            if isinstance(rt.brain, ScriptedBrain)
            else f"model={s.interview_model}",
        },
        "recall": {
            "live": bool(s.recall_api_key),
            "detail": f"region={s.recall_region}, transport={s.recall_realtime_transport}",
        },
        "recall_webhook_verification": {
            "live": bool(s.recall_webhook_secret),
            "detail": "set RECALL_WEBHOOK_SECRET to verify inbound webhooks",
        },
        "tts": {
            "live": not isinstance(rt.tts, SilentTTS),
            "detail": f"provider={s.tts_provider}",
        },
        "voice_output": {
            "live": not isinstance(rt.voice, LoggingVoice),
            "detail": "logging only — set RECALL_API_KEY to speak in a meeting"
            if isinstance(rt.voice, LoggingVoice)
            else f"mode={s.voice_output_mode}",
        },
        "google_calendar": {
            "live": not isinstance(rt.calendar, ManualCalendarClient),
            "detail": "manual Meet links — set GOOGLE_SERVICE_ACCOUNT_FILE"
            if isinstance(rt.calendar, ManualCalendarClient)
            else f"impersonating {s.google_impersonated_user}",
        },
        "google_sheets": {
            "live": not isinstance(rt.sheets, NullSheetsSink),
            "detail": f"spreadsheet={s.sheets_spreadsheet_id or 'unset'}",
        },
        "monday": {
            "live": not isinstance(rt.monday, NullMondaySink),
            "detail": f"board={s.monday_board_id or 'unset'}",
        },
        "slack": {"live": bool(s.slack_webhook_url), "detail": "optional"},
        "email": {"live": bool(s.smtp_host), "detail": "optional"},
    }
    return {
        "env": s.env,
        "public_base_url": s.public_base_url,
        "voice_output_mode": s.voice_output_mode,
        "all_live": all(c["live"] for c in checks.values()),
        "checks": checks,
    }

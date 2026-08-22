"""Inbound Recall.ai traffic.

Two distinct streams, with different verification and different purposes:

  /api/webhooks/recall/status    Svix-delivered bot lifecycle events
                                 (bot.in_call_recording, bot.done, bot.fatal).
                                 Configured once in the Recall dashboard.

  /api/webhooks/recall/realtime  Per-bot real-time events (transcript.data,
                                 participant join/leave). The URL is registered
                                 in each Create Bot call, and carries ?token=.

Both acknowledge fast and do the work in a background task — Recall times
webhook deliveries out at 15 s and retries anything it does not see a 2xx for.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import runtime as get_runtime
from app.db import session_scope
from app.models import Interview, WebhookEvent
from app.runtime import Runtime
from app.security import require_recall_webhook, verify_realtime_token

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks/recall", tags=["webhooks"])

# Bot lifecycle events we act on. Everything else is logged and ignored.
START_EVENTS = {"bot.in_call_recording"}
END_EVENTS = {"bot.done", "bot.call_ended"}
FATAL_EVENTS = {"bot.fatal"}


def _already_seen(request: Request) -> bool:
    """Recall retries for up to 24 h; `webhook-id` is the dedupe key."""
    msg_id = request.headers.get("webhook-id") or request.headers.get("svix-id")
    if not msg_id:
        return False
    with session_scope() as session:
        if session.get(WebhookEvent, msg_id) is not None:
            return True
        session.add(WebhookEvent(id=msg_id, event=None))
    return False


def _interview_id_for_bot(bot_id: str | None, metadata: dict[str, Any] | None) -> str | None:
    """Prefer the id we stamped into bot metadata; fall back to a bot_id lookup."""
    if metadata and metadata.get("interview_id"):
        return str(metadata["interview_id"])
    if not bot_id:
        return None
    with session_scope() as session:
        return session.scalar(select(Interview.id).where(Interview.bot_id == bot_id).limit(1))


# --------------------------------------------------------------------------- #
# Bot status webhooks
# --------------------------------------------------------------------------- #
@router.post("/status", status_code=status.HTTP_200_OK)
async def bot_status(
    request: Request,
    background: BackgroundTasks,
    _: None = Depends(require_recall_webhook),
    rt: Runtime = Depends(get_runtime),
) -> dict[str, str]:
    payload = await request.json()
    event = payload.get("event", "")
    data = payload.get("data", {}) or {}
    bot = data.get("bot", {}) or {}
    bot_id = bot.get("id")
    interview_id = _interview_id_for_bot(bot_id, bot.get("metadata"))

    if _already_seen(request):
        return {"status": "duplicate"}

    log.info("bot status %s bot=%s interview=%s", event, bot_id, (interview_id or "?")[:8])
    if interview_id is None:
        log.warning("no interview matches bot %s — ignoring %s", bot_id, event)
        return {"status": "unmatched"}

    inner = data.get("data", {}) or {}
    if event in START_EVENTS:
        background.add_task(rt.engine.start, interview_id)
    elif event in FATAL_EVENTS:
        background.add_task(
            rt.engine.fail,
            interview_id,
            error=f"{inner.get('code', event)}/{inner.get('sub_code') or '-'}",
        )
    elif event in END_EVENTS:
        # bot.call_ended fires before media is finalised; bot.done is the real
        # end. Acting on either is safe because finish() is idempotent.
        background.add_task(
            rt.engine.finish, interview_id, reason=inner.get("sub_code") or event
        )

    return {"status": "accepted"}


# --------------------------------------------------------------------------- #
# Real-time endpoint (webhook transport)
# --------------------------------------------------------------------------- #
@router.post("/realtime/", status_code=status.HTTP_200_OK)
@router.post("/realtime", status_code=status.HTTP_200_OK, include_in_schema=False)
async def realtime(
    request: Request,
    background: BackgroundTasks,
    token: str | None = None,
    rt: Runtime = Depends(get_runtime),
) -> dict[str, str]:
    if not verify_realtime_token(token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid token")

    payload = await request.json()
    handled = await handle_realtime_event(payload, rt, background=background)
    return {"status": handled}


async def handle_realtime_event(
    payload: dict[str, Any], rt: Runtime, background: BackgroundTasks | None = None
) -> str:
    """Shared by the webhook route and the websocket route."""
    event = payload.get("event", "")
    data = payload.get("data", {}) or {}
    bot = data.get("bot", {}) or {}
    interview_id = _interview_id_for_bot(bot.get("id"), bot.get("metadata"))
    if interview_id is None:
        log.warning("realtime %s for unknown bot %s", event, bot.get("id"))
        return "unmatched"

    inner = data.get("data", {}) or {}
    participant = inner.get("participant") or {}

    if event == "transcript.data":
        text = " ".join(
            (word.get("text") or "") for word in (inner.get("words") or [])
        ).strip()
        if not text:
            return "empty"
        started = ((inner.get("words") or [{}])[0].get("start_timestamp") or {}).get("relative")
        coro = rt.engine.on_utterance(
            interview_id,
            text=text,
            speaker_name=participant.get("name"),
            started_at_s=started,
        )
        if background is not None:
            background.add_task(_await, coro)
        else:
            await coro
        return "transcript"

    if event == "participant_events.join":
        log.info("[%s] participant joined: %s", interview_id[:8], participant.get("name"))
        return "join"

    if event == "participant_events.leave":
        log.info("[%s] participant left: %s", interview_id[:8], participant.get("name"))
        return "leave"

    log.debug("ignoring realtime event %s", event)
    return "ignored"


async def _await(coro) -> None:
    try:
        await coro
    except Exception:
        log.exception("realtime background task failed")

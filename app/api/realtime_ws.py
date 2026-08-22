"""Real-time websocket transport (alternative to the realtime webhook).

Lower latency than webhooks, and Recall keeps the connection warm with an
automatic 30-attempt / 3-second-interval retry policy on failure.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.webhooks import handle_realtime_event
from app.security import verify_realtime_token

log = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


@router.websocket("/api/ws/recall/")
async def recall_socket(websocket: WebSocket, token: str | None = None) -> None:
    if not verify_realtime_token(token):
        await websocket.close(code=4401, reason="invalid token")
        return
    await websocket.accept()
    rt = websocket.app.state.runtime
    log.info("recall realtime websocket connected")
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("non-JSON frame on realtime socket")
                continue
            try:
                await handle_realtime_event(payload, rt)
            except Exception:
                log.exception("failed handling realtime websocket event")
    except WebSocketDisconnect:
        log.info("recall realtime websocket disconnected")

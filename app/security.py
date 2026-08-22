"""Request verification.

Two independent checks:
  * `verify_recall_signature` — HMAC-SHA256 over `{id}.{timestamp}.{body}`,
    the Svix scheme Recall uses for webhooks, websockets and callbacks.
  * `require_internal_key` — a shared secret on our own admin/booking routes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

from fastapi import Header, HTTPException, Request, status

from app.config import get_settings

log = logging.getLogger(__name__)

TOLERANCE_SECONDS = 5 * 60


def _expected_signature(secret: str, msg_id: str, timestamp: str, body: bytes) -> str:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = b".".join([msg_id.encode(), timestamp.encode(), body])
    return base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


def verify_recall_signature(
    *, secret: str | None, headers: dict[str, str], body: bytes
) -> tuple[bool, str]:
    """Returns (ok, reason). `ok=True` with reason 'unverified' when no secret is
    configured — the caller decides whether to allow that."""
    if not secret:
        return True, "unverified"

    lower = {k.lower(): v for k, v in headers.items()}
    msg_id = lower.get("webhook-id") or lower.get("svix-id")
    timestamp = lower.get("webhook-timestamp") or lower.get("svix-timestamp")
    signature_header = lower.get("webhook-signature") or lower.get("svix-signature")
    if not (msg_id and timestamp and signature_header):
        return False, "missing signature headers"

    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        return False, "bad timestamp"
    if age > TOLERANCE_SECONDS:
        return False, f"timestamp too old ({age:.0f}s)"

    expected = _expected_signature(secret, msg_id, timestamp, body)
    for versioned in signature_header.split():
        version, _, candidate = versioned.partition(",")
        if version == "v1" and hmac.compare_digest(candidate, expected):
            return True, "ok"
    return False, "signature mismatch"


def verify_realtime_token(token: str | None) -> bool:
    expected = get_settings().recall_realtime_token
    return bool(token) and hmac.compare_digest(token or "", expected)


async def require_recall_webhook(request: Request) -> None:
    """FastAPI dependency for Recall-originated webhooks."""
    settings = get_settings()
    body = await request.body()
    ok, reason = verify_recall_signature(
        secret=settings.recall_webhook_secret, headers=dict(request.headers), body=body
    )
    if not ok:
        log.warning("rejecting recall webhook: %s", reason)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid signature")
    if reason == "unverified" and settings.env == "prod":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="RECALL_WEBHOOK_SECRET must be set in production",
        )


def require_internal_key(x_api_key: str = Header(default="")) -> None:
    expected = get_settings().internal_api_key
    if not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid X-API-Key")

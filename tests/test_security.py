from __future__ import annotations

import base64
import time

from app.security import _expected_signature, verify_realtime_token, verify_recall_signature

SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
BODY = b'{"event":"bot.done"}'


def _headers(body: bytes = BODY, msg_id: str = "msg_1", timestamp: str | None = None) -> dict:
    timestamp = timestamp or str(int(time.time()))
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": f"v1,{_expected_signature(SECRET, msg_id, timestamp, body)}",
    }


def test_valid_signature_passes():
    assert verify_recall_signature(secret=SECRET, headers=_headers(), body=BODY) == (True, "ok")


def test_svix_prefixed_headers_are_accepted():
    headers = {k.replace("webhook-", "svix-"): v for k, v in _headers().items()}
    ok, _ = verify_recall_signature(secret=SECRET, headers=headers, body=BODY)
    assert ok


def test_tampered_body_is_rejected():
    ok, reason = verify_recall_signature(secret=SECRET, headers=_headers(), body=b'{"evil":1}')
    assert not ok and reason == "signature mismatch"


def test_replayed_old_timestamp_is_rejected():
    old = str(int(time.time()) - 3600)
    ok, reason = verify_recall_signature(
        secret=SECRET, headers=_headers(timestamp=old), body=BODY
    )
    assert not ok and "too old" in reason


def test_missing_headers_are_rejected():
    ok, reason = verify_recall_signature(secret=SECRET, headers={}, body=BODY)
    assert not ok and "missing" in reason


def test_no_secret_configured_reports_unverified():
    assert verify_recall_signature(secret=None, headers={}, body=BODY) == (True, "unverified")


def test_unknown_signature_version_is_rejected():
    headers = _headers()
    headers["webhook-signature"] = headers["webhook-signature"].replace("v1,", "v9,")
    ok, _ = verify_recall_signature(secret=SECRET, headers=headers, body=BODY)
    assert not ok


def test_realtime_token_comparison():
    assert verify_realtime_token("test-realtime-token")
    assert not verify_realtime_token("wrong")
    assert not verify_realtime_token(None)

"""Shared Google credentials.

Uses a service account with domain-wide delegation, impersonating a Workspace
user (`GOOGLE_IMPERSONATED_USER`). Impersonation is what lets the event be
*owned* by a real mailbox — a bare service account cannot create Google Meet
conferences or send invitations.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class GoogleNotConfigured(RuntimeError):
    pass


def build_credentials(scopes: list[str], settings: Settings | None = None):
    settings = settings or get_settings()
    if not settings.google_service_account_file:
        raise GoogleNotConfigured("GOOGLE_SERVICE_ACCOUNT_FILE is not configured")
    from google.oauth2 import service_account  # type: ignore[import-untyped]

    creds = service_account.Credentials.from_service_account_file(
        settings.google_service_account_file, scopes=scopes
    )
    if settings.google_impersonated_user:
        creds = creds.with_subject(settings.google_impersonated_user)
    return creds


# Discovery-document builds are slow, so memoise per (api, credential identity).
# Settings is not hashable, so the key is built from the fields that matter.
_service_cache: dict[tuple[str, ...], Any] = {}


def _build_service(api: str, version: str, scopes: list[str], settings: Settings | None):
    settings = settings or get_settings()
    key = (
        api,
        version,
        settings.google_service_account_file or "",
        settings.google_impersonated_user or "",
    )
    if key not in _service_cache:
        from googleapiclient.discovery import build  # type: ignore[import-untyped]

        _service_cache[key] = build(
            api, version, credentials=build_credentials(scopes, settings), cache_discovery=False
        )
    return _service_cache[key]


def calendar_service(settings: Settings | None = None):
    return _build_service("calendar", "v3", CALENDAR_SCOPES, settings)


def sheets_service(settings: Settings | None = None):
    return _build_service("sheets", "v4", SHEETS_SCOPES, settings)

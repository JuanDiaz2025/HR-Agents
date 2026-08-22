"""Google Sheets sink (box "3. Update Applicant Record")."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from app.clients.google_auth import GoogleNotConfigured, sheets_service
from app.config import Settings, get_settings

log = logging.getLogger(__name__)

HEADER = [
    "Interview ID",
    "Candidate",
    "Email",
    "Phone",
    "Role",
    "Scheduled At",
    "Status",
    "Duration (s)",
    "Overall Score",
    "Recommendation",
    "Disqualified",
    "Disqualification Reasons",
    "Summary",
    "Strengths",
    "Concerns",
    "Category Scores",
    "Meeting URL",
    "Bot ID",
]


class SheetsSink(Protocol):
    def ensure_header(self) -> None: ...
    def upsert_row(self, row: dict[str, Any]) -> int | None: ...


def row_to_values(row: dict[str, Any]) -> list[Any]:
    return [row.get(col, "") for col in HEADER]


class GoogleSheetsSink:
    def __init__(self, settings: Settings | None = None, service=None):
        self.settings = settings or get_settings()
        if not self.settings.sheets_spreadsheet_id:
            raise GoogleNotConfigured("SHEETS_SPREADSHEET_ID is not configured")
        self._service = service or sheets_service(self.settings)

    @property
    def _range(self) -> str:
        return f"{self.settings.sheets_tab_name}!A:R"

    def ensure_header(self) -> None:
        values = (
            self._service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.settings.sheets_spreadsheet_id,
                range=f"{self.settings.sheets_tab_name}!A1:R1",
            )
            .execute()
            .get("values", [])
        )
        if values and values[0] == HEADER:
            return
        self._service.spreadsheets().values().update(
            spreadsheetId=self.settings.sheets_spreadsheet_id,
            range=f"{self.settings.sheets_tab_name}!A1",
            valueInputOption="RAW",
            body={"values": [HEADER]},
        ).execute()
        log.info("wrote sheet header row")

    def _find_row(self, interview_id: str) -> int | None:
        """1-indexed sheet row for an interview, or None. Column A holds the id."""
        values = (
            self._service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.settings.sheets_spreadsheet_id,
                range=f"{self.settings.sheets_tab_name}!A:A",
            )
            .execute()
            .get("values", [])
        )
        for idx, row in enumerate(values, start=1):
            if row and row[0] == interview_id:
                return idx
        return None

    def upsert_row(self, row: dict[str, Any]) -> int | None:
        """Idempotent write keyed on Interview ID — re-running the pipeline
        updates the existing row instead of appending a duplicate."""
        self.ensure_header()
        values = [row_to_values(row)]
        interview_id = str(row.get("Interview ID", ""))
        existing = self._find_row(interview_id) if interview_id else None

        if existing:
            self._service.spreadsheets().values().update(
                spreadsheetId=self.settings.sheets_spreadsheet_id,
                range=f"{self.settings.sheets_tab_name}!A{existing}",
                valueInputOption="RAW",
                body={"values": values},
            ).execute()
            return existing

        response = (
            self._service.spreadsheets()
            .values()
            .append(
                spreadsheetId=self.settings.sheets_spreadsheet_id,
                range=self._range,
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            )
            .execute()
        )
        updated = response.get("updates", {}).get("updatedRange", "")
        try:
            return int("".join(ch for ch in updated.split("!")[-1].split(":")[0] if ch.isdigit()))
        except (ValueError, IndexError):
            return None


class NullSheetsSink:
    def ensure_header(self) -> None:
        return None

    def upsert_row(self, row: dict[str, Any]) -> int | None:
        log.info("[no sheets] would write row: %s", {k: row.get(k) for k in HEADER[:10]})
        return None


def build_sheets_sink(settings: Settings | None = None) -> SheetsSink:
    settings = settings or get_settings()
    try:
        return GoogleSheetsSink(settings)
    except GoogleNotConfigured as exc:
        log.warning("%s — skipping Google Sheets writes", exc)
        return NullSheetsSink()
    except Exception:
        log.exception("could not build Google Sheets client — skipping writes")
        return NullSheetsSink()

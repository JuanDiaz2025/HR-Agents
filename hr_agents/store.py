"""The spreadsheet, behind an interface.

`docs/spreadsheet_schema.md` is the contract this implements. Two rules matter
more than the transport: the pipeline writes only to its own columns, and a row
a human has already decided is never touched again.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol

from .models import ScoredEvaluation

SUBMISSION_ID = "Submission ID"
VIDEO_LINK = "Video Link"
STATUS = "Status"
REVIEWER_DECISION = "Reviewer Decision"

# Columns the pipeline owns. Nothing outside this set is ever written.
PIPELINE_COLUMNS = (
    STATUS,
    "AI Score",
    "AI Decision",
    "Confidence",
    "Feedback",
    "Strengths",
    "Areas to Improve",
    "Flags",
    "Criterion Scores",
    "Rubric Version",
    "Evaluated At",
    "Error",
)

# Columns the pipeline never treats as part of the applicant's form response.
NON_FORM_COLUMNS = frozenset(
    PIPELINE_COLUMNS
    + (SUBMISSION_ID, VIDEO_LINK, REVIEWER_DECISION, "Reviewer", "Reviewer Notes", "Reviewed At")
)


@dataclass
class Submission:
    row_number: int
    values: dict[str, str] = field(default_factory=dict)

    @property
    def submission_id(self) -> str:
        return self.values.get(SUBMISSION_ID, "").strip() or f"row-{self.row_number}"

    @property
    def video_link(self) -> str:
        return self.values.get(VIDEO_LINK, "").strip()

    @property
    def status(self) -> str:
        return self.values.get(STATUS, "").strip().upper()

    @property
    def reviewer_decision(self) -> str:
        return self.values.get(REVIEWER_DECISION, "").strip()

    def form_response(self) -> dict[str, str]:
        """Only what the applicant actually submitted — no pipeline bookkeeping."""
        return {
            key: value
            for key, value in self.values.items()
            if key not in NON_FORM_COLUMNS and str(value).strip()
        }

    def is_pending(self) -> bool:
        if self.reviewer_decision:
            return False  # a human has decided; never overwrite
        if not self.video_link:
            return False  # no video yet — stage 4 has not run
        return self.status in ("", "PENDING", "ERROR")


class SubmissionStore(Protocol):
    def pending(self) -> list[Submission]: ...
    def update(self, submission: Submission, values: dict[str, str]) -> None: ...


def result_columns(evaluation: ScoredEvaluation) -> dict[str, str]:
    """Map a scored evaluation onto the pipeline's spreadsheet columns."""
    result = evaluation.result
    return {
        STATUS: "DONE",
        "AI Score": str(evaluation.score),
        "AI Decision": evaluation.decision,
        "Confidence": str(result.confidence),
        "Feedback": result.summary,
        "Strengths": "; ".join(result.strengths),
        "Areas to Improve": "; ".join(result.areas_to_improve),
        "Flags": "; ".join(result.flags),
        "Criterion Scores": json.dumps(
            [c.model_dump() for c in result.criteria], ensure_ascii=False
        ),
        "Rubric Version": str(evaluation.rubric_version),
        "Evaluated At": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "Error": "",
    }


def error_columns(message: str) -> dict[str, str]:
    return {
        STATUS: "ERROR",
        "Error": message[:1000],
        "Evaluated At": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


class CsvStore:
    """A CSV file standing in for the spreadsheet. For local development and tests."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _read(self) -> tuple[list[str], list[dict[str, str]]]:
        with self.path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            rows = [{k: (v or "") for k, v in row.items()} for row in reader]
        for column in PIPELINE_COLUMNS:
            if column not in headers:
                headers.append(column)
                for row in rows:
                    row.setdefault(column, "")
        return headers, rows

    def _write(self, headers: list[str], rows: Iterable[dict[str, str]]) -> None:
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow({h: row.get(h, "") for h in headers})

    def pending(self) -> list[Submission]:
        _, rows = self._read()
        submissions = [Submission(row_number=i + 2, values=row) for i, row in enumerate(rows)]
        return [s for s in submissions if s.is_pending()]

    def update(self, submission: Submission, values: dict[str, str]) -> None:
        unknown = set(values) - set(PIPELINE_COLUMNS)
        if unknown:
            raise ValueError(f"Refusing to write non-pipeline columns: {sorted(unknown)}")
        headers, rows = self._read()
        index = submission.row_number - 2
        rows[index].update(values)
        submission.values.update(values)
        self._write(headers, rows)


class GoogleSheetsStore:
    """The real store: a worksheet addressed by header name, not column letter.

    Reading the header row on every call is deliberate — adding a form question
    shifts every column after it, and a cached mapping would start writing scores
    into someone's free-text answer.
    """

    def __init__(self, spreadsheet_id: str, worksheet: str, service=None):
        self.spreadsheet_id = spreadsheet_id
        self.worksheet = worksheet
        self._service = service

    @property
    def service(self):
        if self._service is None:
            self._service = build_sheets_service()
        return self._service

    def _values(self):
        return self.service.spreadsheets().values()

    def _read(self) -> tuple[list[str], list[list[str]]]:
        response = (
            self._values()
            .get(spreadsheetId=self.spreadsheet_id, range=f"'{self.worksheet}'")
            .execute()
        )
        rows = response.get("values", [])
        if not rows:
            return [], []
        return [str(h).strip() for h in rows[0]], rows[1:]

    def _ensure_columns(self, headers: list[str]) -> list[str]:
        missing = [c for c in PIPELINE_COLUMNS if c not in headers]
        if not missing:
            return headers
        headers = headers + missing
        self._values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{self.worksheet}'!1:1",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()
        return headers

    def pending(self) -> list[Submission]:
        headers, rows = self._read()
        if not headers:
            return []
        headers = self._ensure_columns(headers)
        submissions = []
        for offset, row in enumerate(rows):
            padded = list(row) + [""] * (len(headers) - len(row))
            values = {h: str(v) for h, v in zip(headers, padded)}
            submissions.append(Submission(row_number=offset + 2, values=values))
        return [s for s in submissions if s.is_pending()]

    def update(self, submission: Submission, values: dict[str, str]) -> None:
        unknown = set(values) - set(PIPELINE_COLUMNS)
        if unknown:
            raise ValueError(f"Refusing to write non-pipeline columns: {sorted(unknown)}")
        headers, _ = self._read()
        headers = self._ensure_columns(headers)

        data = []
        for column, value in values.items():
            letter = _column_letter(headers.index(column) + 1)
            data.append(
                {
                    "range": f"'{self.worksheet}'!{letter}{submission.row_number}",
                    "values": [[value]],
                }
            )
        self._values().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
        submission.values.update(values)


def _column_letter(index: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    if index < 1:
        raise ValueError("Column index is 1-based.")
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
)


def _credentials():
    from google.auth import default  # imported lazily

    credentials, _ = default(scopes=list(SCOPES))
    return credentials


def build_sheets_service():
    from googleapiclient.discovery import build  # imported lazily

    return build("sheets", "v4", credentials=_credentials(), cache_discovery=False)


def build_drive_service():
    from googleapiclient.discovery import build  # imported lazily

    return build("drive", "v3", credentials=_credentials(), cache_discovery=False)

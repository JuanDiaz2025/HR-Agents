"""The spreadsheet, behind an interface.

`docs/spreadsheet_schema.md` is the contract this implements. Three rules matter
more than the transport: the pipeline writes only pipeline columns, reviewers
write only reviewer columns, and a row a human has decided is never re-evaluated.
"""

from __future__ import annotations

import csv
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import ScoredEvaluation

SUBMISSION_ID = "Submission ID"
VIDEO_LINK = "Video Link"
STATUS = "Status"
REVIEWER_DECISION = "Reviewer Decision"

# Columns the pipeline owns. Nothing outside this set is ever written by a run.
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

# Columns a human reviewer owns. The pipeline never touches these.
REVIEWER_COLUMNS = ("Reviewer", REVIEWER_DECISION, "Reviewer Notes", "Reviewed At")

MANAGED_COLUMNS = PIPELINE_COLUMNS + REVIEWER_COLUMNS

# Never part of the applicant's form response. Timestamp is form bookkeeping —
# the submission time tells the model nothing about the video.
NON_FORM_COLUMNS = frozenset(MANAGED_COLUMNS + (SUBMISSION_ID, VIDEO_LINK, "Timestamp"))

# Additionally redundant on the detail page, where they already head the record.
IDENTITY_COLUMNS = ("Name", "Email")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Submission:
    row_number: int
    values: dict[str, str] = field(default_factory=dict)

    def get(self, column: str) -> str:
        return str(self.values.get(column, "")).strip()

    @property
    def submission_id(self) -> str:
        return self.get(SUBMISSION_ID) or f"row-{self.row_number}"

    @property
    def name(self) -> str:
        return self.get("Name") or self.submission_id

    @property
    def email(self) -> str:
        return self.get("Email")

    @property
    def video_link(self) -> str:
        return self.get(VIDEO_LINK)

    @property
    def status(self) -> str:
        return self.get(STATUS).upper()

    @property
    def ai_decision(self) -> str:
        return self.get("AI Decision")

    @property
    def reviewer_decision(self) -> str:
        return self.get(REVIEWER_DECISION)

    @property
    def final_decision(self) -> str:
        """The reviewer's call when there is one, otherwise the AI's."""
        return self.reviewer_decision or self.ai_decision

    @property
    def was_overridden(self) -> bool:
        return bool(self.reviewer_decision and self.ai_decision) and (
            self.reviewer_decision != self.ai_decision
        )

    @property
    def score(self) -> int | None:
        raw = self.get("AI Score")
        return int(raw) if raw.isdigit() else None

    @property
    def confidence(self) -> int | None:
        raw = self.get("Confidence")
        return int(raw) if raw.isdigit() else None

    @property
    def flags(self) -> list[str]:
        return [f.strip() for f in self.get("Flags").split(";") if f.strip()]

    @property
    def strengths(self) -> list[str]:
        return [s.strip() for s in self.get("Strengths").split(";") if s.strip()]

    @property
    def areas_to_improve(self) -> list[str]:
        return [s.strip() for s in self.get("Areas to Improve").split(";") if s.strip()]

    def criterion_scores(self) -> list[dict]:
        raw = self.get("Criterion Scores")
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    def form_response(self) -> dict[str, str]:
        """Only what the applicant submitted — no pipeline bookkeeping."""
        return {
            key: value
            for key, value in self.values.items()
            if key not in NON_FORM_COLUMNS and str(value).strip()
        }

    def application_answers(self) -> dict[str, str]:
        """The form response minus the fields already shown elsewhere on the page."""
        return {
            key: value
            for key, value in self.form_response().items()
            if key not in IDENTITY_COLUMNS
        }

    def is_pending(self) -> bool:
        if self.reviewer_decision:
            return False  # a human has decided; never overwrite
        if not self.video_link:
            return False  # no video yet — stage 4 has not run
        return self.status in ("", "PENDING", "ERROR")


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
        "Evaluated At": _now(),
        "Error": "",
    }


def error_columns(message: str) -> dict[str, str]:
    return {STATUS: "ERROR", "Error": message[:1000], "Evaluated At": _now()}


def review_columns(decision: str, reviewer: str, notes: str = "") -> dict[str, str]:
    return {
        REVIEWER_DECISION: decision,
        "Reviewer": reviewer,
        "Reviewer Notes": notes,
        "Reviewed At": _now(),
    }


class SubmissionStore(ABC):
    """Read every row; write only the columns the caller is allowed to write."""

    @abstractmethod
    def all(self) -> list[Submission]: ...

    @abstractmethod
    def _write(self, submission: Submission, values: dict[str, str]) -> None: ...

    @abstractmethod
    def append(self, values: dict[str, str]) -> Submission: ...

    def pending(self) -> list[Submission]:
        return [s for s in self.all() if s.is_pending()]

    def get(self, submission_id: str) -> Submission | None:
        for submission in self.all():
            if submission.submission_id == submission_id:
                return submission
        return None

    def update(self, submission: Submission, values: dict[str, str]) -> None:
        """Write pipeline columns. Used by the evaluation run."""
        self._guard(values, PIPELINE_COLUMNS, "pipeline")
        self._write(submission, values)

    def record_review(self, submission: Submission, values: dict[str, str]) -> None:
        """Write reviewer columns. Used by a human in the app."""
        self._guard(values, REVIEWER_COLUMNS, "reviewer")
        self._write(submission, values)

    @staticmethod
    def _guard(values: dict[str, str], allowed: Iterable[str], label: str) -> None:
        unknown = set(values) - set(allowed)
        if unknown:
            raise ValueError(f"Refusing to write non-{label} columns: {sorted(unknown)}")


class CsvStore(SubmissionStore):
    """A CSV file standing in for the spreadsheet. Local development and tests."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _read(self) -> tuple[list[str], list[dict[str, str]]]:
        if not self.path.exists():
            return [], []
        with self.path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            rows = [{k: (v or "") for k, v in row.items() if k is not None} for row in reader]
        for column in MANAGED_COLUMNS:
            if column not in headers:
                headers.append(column)
        for row in rows:
            for column in headers:
                row.setdefault(column, "")
        return headers, rows

    def _save(self, headers: list[str], rows: Iterable[dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow({h: row.get(h, "") for h in headers})

    def all(self) -> list[Submission]:
        _, rows = self._read()
        return [Submission(row_number=i + 2, values=row) for i, row in enumerate(rows)]

    def _write(self, submission: Submission, values: dict[str, str]) -> None:
        headers, rows = self._read()
        rows[submission.row_number - 2].update(values)
        submission.values.update(values)
        self._save(headers, rows)

    def append(self, values: dict[str, str]) -> Submission:
        headers, rows = self._read()
        for key in values:
            if key not in headers:
                headers.append(key)
        row = {h: str(values.get(h, "")) for h in headers}
        rows.append(row)
        self._save(headers, rows)
        return Submission(row_number=len(rows) + 1, values=row)


class GoogleSheetsStore(SubmissionStore):
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
        missing = [c for c in MANAGED_COLUMNS if c not in headers]
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

    def all(self) -> list[Submission]:
        headers, rows = self._read()
        if not headers:
            return []
        headers = self._ensure_columns(headers)
        submissions = []
        for offset, row in enumerate(rows):
            padded = list(row) + [""] * (len(headers) - len(row))
            submissions.append(
                Submission(
                    row_number=offset + 2,
                    values={h: str(v) for h, v in zip(headers, padded)},
                )
            )
        return submissions

    def _write(self, submission: Submission, values: dict[str, str]) -> None:
        headers, _ = self._read()
        headers = self._ensure_columns(headers)
        data = [
            {
                "range": (
                    f"'{self.worksheet}'!"
                    f"{_column_letter(headers.index(column) + 1)}{submission.row_number}"
                ),
                "values": [[value]],
            }
            for column, value in values.items()
        ]
        self._values().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
        submission.values.update(values)

    def append(self, values: dict[str, str]) -> Submission:
        headers, rows = self._read()
        headers = self._ensure_columns(headers or list(values))
        unknown = [key for key in values if key not in headers]
        if unknown:
            raise ValueError(
                f"Sheet has no column for {unknown}. Add the column to the sheet first — "
                "the app will not reshape a spreadsheet other systems write to."
            )
        self._values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{self.worksheet}'",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [[str(values.get(h, "")) for h in headers]]},
        ).execute()
        return Submission(
            row_number=len(rows) + 2, values={h: str(values.get(h, "")) for h in headers}
        )


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

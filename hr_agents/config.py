"""Runtime settings, read from the environment once at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

VALID_EFFORT = ("low", "medium", "high", "xhigh", "max")


class ConfigError(ValueError):
    """A required setting is missing or invalid."""


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    model: str = "claude-opus-5"
    effort: str = "high"
    rubric_path: Path = Path("config/rubric.yaml")
    frame_count: int = 6
    max_video_seconds: int = 900
    store: str = "csv"
    csv_path: Path = Path("submissions.csv")
    spreadsheet_id: str = ""
    worksheet: str = "Form Responses 1"
    whisper_model: str = "base.en"
    workdir: Path = Path("workdir")

    @classmethod
    def from_env(cls) -> "Settings":
        effort = os.environ.get("HR_AGENTS_EFFORT", "high").strip().lower()
        if effort not in VALID_EFFORT:
            raise ConfigError(f"HR_AGENTS_EFFORT must be one of {VALID_EFFORT}, got {effort!r}")

        store = os.environ.get("HR_AGENTS_STORE", "csv").strip().lower()
        if store not in ("csv", "sheets"):
            raise ConfigError(f"HR_AGENTS_STORE must be 'csv' or 'sheets', got {store!r}")

        spreadsheet_id = os.environ.get("HR_AGENTS_SPREADSHEET_ID", "").strip()
        if store == "sheets" and not spreadsheet_id:
            raise ConfigError("HR_AGENTS_STORE=sheets requires HR_AGENTS_SPREADSHEET_ID.")

        return cls(
            model=os.environ.get("HR_AGENTS_MODEL", "claude-opus-5").strip(),
            effort=effort,
            rubric_path=Path(os.environ.get("HR_AGENTS_RUBRIC", "config/rubric.yaml")),
            frame_count=_int("HR_AGENTS_FRAME_COUNT", 6),
            max_video_seconds=_int("HR_AGENTS_MAX_VIDEO_SECONDS", 900),
            store=store,
            csv_path=Path(os.environ.get("HR_AGENTS_CSV_PATH", "submissions.csv")),
            spreadsheet_id=spreadsheet_id,
            worksheet=os.environ.get("HR_AGENTS_WORKSHEET", "Form Responses 1"),
            whisper_model=os.environ.get("HR_AGENTS_WHISPER_MODEL", "base.en"),
            workdir=Path(os.environ.get("HR_AGENTS_WORKDIR", "workdir")),
        )

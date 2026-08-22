"""Application configuration.

Every integration is optional. If a credential is missing the corresponding
client falls back to a no-op/log-only implementation, so the app boots and the
interview engine stays testable without any third-party account.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # ------------------------------------------------------------------ app
    app_name: str = "AI Interviewer"
    env: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"

    # Public base URL of THIS service. Recall.ai calls back into it, so it must
    # be reachable from the internet (ngrok static domain in dev).
    public_base_url: str = "http://localhost:8000"

    # Shared secret for our own admin/booking endpoints (X-API-Key header).
    internal_api_key: str = "dev-internal-key"

    database_url: str = "sqlite+pysqlite:///./hr_agents.db"

    # -------------------------------------------------------------- recall.ai
    recall_api_key: str | None = None
    # us-east-1 | us-west-2 | eu-central-1 | ap-northeast-1
    recall_region: str = "us-west-2"
    # Workspace verification secret ("whsec_...") used to verify webhooks,
    # websockets and callbacks from Recall.
    recall_webhook_secret: str | None = None
    # Extra shared token appended to real-time endpoint URLs as ?token=...
    recall_realtime_token: str = "change-me-realtime-token"
    bot_name: str = "AI Interviewer"
    # "webhook" (simplest) or "websocket" (lower latency) transport for
    # real-time transcript delivery.
    recall_realtime_transport: Literal["webhook", "websocket"] = "webhook"
    # How the bot speaks: "output_audio" posts TTS mp3 to Recall;
    # "output_media" streams a webpage we host that does TTS in the browser.
    voice_output_mode: Literal["output_audio", "output_media"] = "output_audio"
    # Leave the meeting automatically after this much silence (seconds).
    bot_silence_timeout_s: int = 300

    # ------------------------------------------------------------------ claude
    anthropic_api_key: str | None = None
    interview_model: str = "claude-opus-5"
    scoring_model: str = "claude-opus-5"
    # low | medium | high | xhigh | max
    interview_effort: str = "low"
    scoring_effort: str = "high"

    # --------------------------------------------------------------------- tts
    tts_provider: Literal["elevenlabs", "openai", "google", "silent"] = "silent"
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    elevenlabs_model_id: str = "eleven_turbo_v2_5"
    openai_api_key: str | None = None
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "alloy"
    google_tts_voice: str = "en-US-Neural2-F"

    # -------------------------------------------------------- google workspace
    # Path to a service-account JSON key with domain-wide delegation.
    google_service_account_file: str | None = None
    # Workspace user the service account impersonates. This mailbox owns the
    # calendar events and is the account Recall's bot is invited as.
    google_impersonated_user: str | None = None
    interviewer_email: str = "interviewer@equitytrackph.com"
    calendar_id: str = "primary"
    interview_timezone: str = "Asia/Manila"
    interview_duration_minutes: int = 30

    # ------------------------------------------------------------ google sheets
    sheets_spreadsheet_id: str | None = None
    sheets_tab_name: str = "Interviews"

    # ---------------------------------------------------------------- monday.com
    monday_api_key: str | None = None
    monday_board_id: str | None = None
    monday_group_proceed: str | None = None
    monday_group_review: str | None = None
    monday_group_rejected: str | None = None
    # Map logical field -> monday column id, e.g.
    # MONDAY_COLUMN_MAP='{"status":"status","score":"numbers","transcript":"link"}'
    monday_column_map: dict[str, str] = Field(default_factory=dict)

    # -------------------------------------------------------------- notifications
    slack_webhook_url: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    hr_notify_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # ------------------------------------------------------- interview behaviour
    knowledge_dir: Path = REPO_ROOT / "knowledge"
    # Seconds of transcript silence after which we treat the answer as finished.
    answer_debounce_s: float = 2.5
    # Hard ceiling on follow-ups per question.
    max_followups_per_question: int = 2
    # Hard ceiling on total questions asked (safety valve).
    max_questions: int = 20
    # Wall-clock cap on the interview (seconds) before we wrap up politely.
    max_interview_seconds: int = 1800
    # Rough words-per-second used to estimate how long our own speech takes,
    # so we can ignore transcript picked up while the bot is talking.
    speech_words_per_second: float = 2.6
    # Pull facts mid-interview after questions in these categories, so a hard
    # blocker ends the call early instead of burning the whole slot. Empty
    # list disables the extra LLM calls.
    early_exit_check_categories: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["availability"]
    )

    @field_validator("early_exit_check_categories", mode="before")
    @classmethod
    def _split_categories(cls, v: object) -> object:
        if isinstance(v, str):
            return [c.strip() for c in v.split(",") if c.strip()]
        return v

    @field_validator("public_base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("hr_notify_emails", mode="before")
    @classmethod
    def _split_emails(cls, v: object) -> object:
        if isinstance(v, str):
            return [e.strip() for e in v.split(",") if e.strip()]
        return v

    # --------------------------------------------------------------- derived
    @property
    def recall_base_url(self) -> str:
        return f"https://{self.recall_region}.recall.ai/api/v1"

    @property
    def realtime_webhook_url(self) -> str:
        return (
            f"{self.public_base_url}/api/webhooks/recall/realtime/"
            f"?token={self.recall_realtime_token}"
        )

    @property
    def realtime_websocket_url(self) -> str:
        scheme = "ws" if self.public_base_url.startswith("http://") else "wss"
        host = self.public_base_url.split("://", 1)[-1]
        return f"{scheme}://{host}/api/ws/recall/?token={self.recall_realtime_token}"

    @property
    def status_webhook_url(self) -> str:
        return f"{self.public_base_url}/api/webhooks/recall/status"


@lru_cache
def get_settings() -> Settings:
    return Settings()

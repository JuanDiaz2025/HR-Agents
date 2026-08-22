"""Optional notifications (Slack + email)."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self._client = client
        self.sent: list[tuple[str, str]] = []  # (channel, body) — handy in tests

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def slack(self, text: str, blocks: list | None = None) -> None:
        url = self.settings.slack_webhook_url
        self.sent.append(("slack", text))
        if not url:
            log.info("[no slack] %s", text)
            return
        payload: dict = {"text": text}
        if blocks:
            payload["blocks"] = blocks
        try:
            response = await (await self._http()).post(url, json=payload)
            response.raise_for_status()
        except Exception:
            log.exception("slack notification failed")

    def email(self, subject: str, body: str, to: list[str] | None = None) -> None:
        recipients = to or self.settings.hr_notify_emails
        self.sent.append(("email", f"{subject}\n{body}"))
        if not (self.settings.smtp_host and recipients):
            log.info("[no smtp] %s -> %s", subject, recipients)
            return
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.smtp_from or (self.settings.smtp_user or "noreply@localhost")
        message["To"] = ", ".join(recipients)
        message.set_content(body)
        try:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=20) as smtp:
                smtp.ehlo()
                if self.settings.smtp_port in (587, 25):
                    smtp.starttls()
                    smtp.ehlo()
                if self.settings.smtp_user:
                    smtp.login(self.settings.smtp_user, self.settings.smtp_password or "")
                smtp.send_message(message)
        except Exception:
            log.exception("email notification failed")

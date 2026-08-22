"""Text-to-speech providers. Each returns mp3 bytes, which is what Recall's
Output Audio endpoint accepts."""

from __future__ import annotations

import base64
import logging
from typing import Protocol

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


class TTSProvider(Protocol):
    async def synthesize(self, text: str) -> bytes: ...
    async def aclose(self) -> None: ...


class SilentTTS:
    """Produces a valid but silent mp3. Lets the whole pipeline run — timing,
    turn-taking, Recall calls — without a TTS bill or credentials."""

    def __init__(self) -> None:
        from app.clients.recall import SILENT_MP3_B64

        self._silence = base64.b64decode(SILENT_MP3_B64)
        self.spoken: list[str] = []

    async def synthesize(self, text: str) -> bytes:
        self.spoken.append(text)
        log.info("[silent-tts] would speak: %s", text)
        return self._silence

    async def aclose(self) -> None:
        return None


class ElevenLabsTTS:
    BASE = "https://api.elevenlabs.io/v1"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        if not settings.elevenlabs_api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not configured")
        self.settings = settings
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))

    async def synthesize(self, text: str) -> bytes:
        response = await self._client.post(
            f"{self.BASE}/text-to-speech/{self.settings.elevenlabs_voice_id}",
            headers={
                "xi-api-key": self.settings.elevenlabs_api_key or "",
                "Accept": "audio/mpeg",
            },
            params={"output_format": "mp3_44100_128"},
            json={
                "text": text,
                "model_id": self.settings.elevenlabs_model_id,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
        )
        response.raise_for_status()
        return response.content

    async def aclose(self) -> None:
        await self._client.aclose()


class OpenAITTS:
    BASE = "https://api.openai.com/v1"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        self.settings = settings
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))

    async def synthesize(self, text: str) -> bytes:
        response = await self._client.post(
            f"{self.BASE}/audio/speech",
            headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
            json={
                "model": self.settings.openai_tts_model,
                "voice": self.settings.openai_tts_voice,
                "input": text,
                "response_format": "mp3",
            },
        )
        response.raise_for_status()
        return response.content

    async def aclose(self) -> None:
        await self._client.aclose()


class GoogleTTS:
    """Google Cloud Text-to-Speech, authenticated with the same service account
    used for Calendar/Sheets (needs the cloud-platform scope)."""

    URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))

    def _token(self) -> str:
        from google.auth.transport.requests import Request  # type: ignore[import-untyped]
        from google.oauth2 import service_account  # type: ignore[import-untyped]

        if not self.settings.google_service_account_file:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_FILE is not configured")
        creds = service_account.Credentials.from_service_account_file(
            self.settings.google_service_account_file,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        creds.refresh(Request())
        return creds.token

    async def synthesize(self, text: str) -> bytes:
        language_code = "-".join(self.settings.google_tts_voice.split("-")[:2])
        response = await self._client.post(
            self.URL,
            headers={"Authorization": f"Bearer {self._token()}"},
            json={
                "input": {"text": text},
                "voice": {"languageCode": language_code, "name": self.settings.google_tts_voice},
                "audioConfig": {"audioEncoding": "MP3", "speakingRate": 1.0},
            },
        )
        response.raise_for_status()
        return base64.b64decode(response.json()["audioContent"])

    async def aclose(self) -> None:
        await self._client.aclose()


def build_tts(settings: Settings | None = None) -> TTSProvider:
    settings = settings or get_settings()
    provider = settings.tts_provider
    try:
        if provider == "elevenlabs":
            return ElevenLabsTTS(settings)
        if provider == "openai":
            return OpenAITTS(settings)
        if provider == "google":
            return GoogleTTS(settings)
    except RuntimeError as exc:
        log.warning("TTS provider %r unavailable (%s) — using silent audio", provider, exc)
        return SilentTTS()
    if provider != "silent":
        log.warning("unknown TTS provider %r — using silent audio", provider)
    return SilentTTS()

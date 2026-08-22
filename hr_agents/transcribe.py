"""Speech-to-text.

The evaluator asks the model to cite timestamps as evidence, so the transcript
is rendered with `[mm:ss]` markers rather than as a flat wall of text. Anything
that can produce segments with start times can be plugged in here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Transcript:
    segments: tuple[Segment, ...]
    language: str | None = None

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()

    def formatted(self) -> str:
        """Timestamped transcript, one line per segment."""
        if not self.segments:
            return "(no speech detected)"
        lines = []
        for segment in self.segments:
            total = int(segment.start)
            lines.append(f"[{total // 60:02d}:{total % 60:02d}] {segment.text.strip()}")
        return "\n".join(lines)


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> Transcript: ...


class FasterWhisperTranscriber:
    """Local transcription via faster-whisper. No API key, no data leaves the box.

    The model is loaded once and reused — loading is by far the slowest part, so
    a per-call load would dominate the runtime of a batch.
    """

    def __init__(self, model_size: str = "base.en", device: str = "auto", compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:  # pragma: no cover - environment dependent
                raise RuntimeError(
                    "faster-whisper is not installed. `pip install 'hr-agents[transcribe]'` "
                    "or supply a different Transcriber."
                ) from exc
            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
        return self._model

    def transcribe(self, audio_path: Path) -> Transcript:
        segments, info = self._load().transcribe(str(audio_path), vad_filter=True)
        return Transcript(
            segments=tuple(
                Segment(start=s.start, end=s.end, text=s.text) for s in segments
            ),
            language=getattr(info, "language", None),
        )


class StaticTranscriber:
    """Returns a fixed transcript. For tests and for dry runs."""

    def __init__(self, transcript: Transcript):
        self._transcript = transcript

    def transcribe(self, audio_path: Path) -> Transcript:  # noqa: ARG002
        return self._transcript

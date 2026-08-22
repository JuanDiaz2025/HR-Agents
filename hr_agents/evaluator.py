"""Stage 5: send the rubric, the transcript, and sampled frames to Claude.

The system prompt (role, rules, rubric) is byte-identical for every submission in
a batch and is marked for caching; only the user turn varies. On a batch of any
size that is most of the input tokens read from cache rather than paid for.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import anthropic

from .media import Frame
from .models import EvaluationResult
from .rubric import Rubric
from .transcribe import Transcript

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "video_evaluation.md"

_SYSTEM_MARKER = "<!-- system -->"
_USER_MARKER = "<!-- user -->"


class PromptError(ValueError):
    """The prompt template is malformed."""


class EvaluationFailed(RuntimeError):
    """The model could not produce a usable evaluation."""


@dataclass(frozen=True)
class PromptTemplate:
    system: str
    user: str

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PROMPT_PATH) -> "PromptTemplate":
        text = Path(path).read_text(encoding="utf-8")
        if _SYSTEM_MARKER not in text or _USER_MARKER not in text:
            raise PromptError(
                f"{path} must contain both {_SYSTEM_MARKER} and {_USER_MARKER} markers."
            )
        _, remainder = text.split(_SYSTEM_MARKER, 1)
        system, user = remainder.split(_USER_MARKER, 1)
        return cls(system=system.strip(), user=user.strip())


def _fill(template: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise PromptError(f"Prompt placeholder {{{{{key}}}}} has no value.")
        return values[key]

    return re.sub(r"\{\{(\w+)\}\}", replace, template)


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}m {total % 60:02d}s"


class Evaluator:
    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        model: str = DEFAULT_MODEL,
        effort: str = "high",
        max_tokens: int = 16000,
        template: PromptTemplate | None = None,
    ):
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.template = template or PromptTemplate.load()

    def build_system(self, rubric: Rubric) -> str:
        prohibited = "\n".join(f"   - {p}" for p in rubric.prohibited_factors)
        return _fill(
            self.template.system,
            {"prohibited_factors": prohibited, "rubric_yaml": rubric.as_yaml().strip()},
        )

    def build_user_text(
        self,
        submission_id: str,
        form_response: dict,
        transcript: Transcript,
        frames: list[Frame],
        duration: float,
    ) -> str:
        return _fill(
            self.template.user,
            {
                "submission_id": submission_id,
                "duration": _format_duration(duration),
                "frame_labels": ", ".join(f.label for f in frames) or "none",
                "form_response_json": json.dumps(form_response, indent=2, ensure_ascii=False),
                "transcript": transcript.formatted(),
            },
        )

    def evaluate(
        self,
        submission_id: str,
        rubric: Rubric,
        form_response: dict,
        transcript: Transcript,
        frames: list[Frame],
        duration: float,
    ) -> EvaluationResult:
        content: list[dict] = [f.as_image_block() for f in frames]
        content.append(
            {
                "type": "text",
                "text": self.build_user_text(
                    submission_id, form_response, transcript, frames, duration
                ),
            }
        )

        response = self.client.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            output_format=EvaluationResult,
            system=[
                {
                    "type": "text",
                    "text": self.build_system(rubric),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": content}],
        )

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise EvaluationFailed(
                f"Model declined to evaluate {submission_id}"
                + (f": {details.category}" if details is not None else "")
            )

        parsed = response.parsed_output
        if parsed is None:
            raise EvaluationFailed(f"No structured output returned for {submission_id}.")

        usage = response.usage
        logger.info(
            "evaluated %s (in=%s cached=%s out=%s)",
            submission_id,
            usage.input_tokens,
            getattr(usage, "cache_read_input_tokens", 0),
            usage.output_tokens,
        )
        return parsed

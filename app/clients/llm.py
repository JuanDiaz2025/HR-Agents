"""The AI brain (box "C") — Claude, via the official Anthropic SDK.

Two calls:
  * `decide_turn`   — per-turn: follow up, move on, answer, or end.
  * `evaluate`      — once after the call: category scores, facts, summary.

Both use structured outputs so the caller never parses free text. When no API
key is configured a deterministic scripted brain takes over, which is what the
tests and the offline simulator run against.
"""

from __future__ import annotations

import logging
from typing import Literal, Protocol

import anthropic
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.interview.knowledge import Knowledge
from app.interview.prompts import (
    build_interviewer_system,
    build_scoring_system,
    build_scoring_user_message,
    build_turn_user_message,
)

log = logging.getLogger(__name__)

TurnAction = Literal["ASK_FOLLOWUP", "NEXT_QUESTION", "ANSWER_AND_CONTINUE", "END_INTERVIEW"]


class TurnDecision(BaseModel):
    """What the interviewer says next, and why."""

    action: TurnAction = Field(description="The single action to take on this turn.")
    speech: str = Field(
        description="Exactly what to say out loud. Plain spoken prose, no markdown."
    )
    # Present when the action advances the plan; lets us keep DB state honest
    # even if the model reworded the question.
    question_id: str | None = Field(
        default=None,
        description=(
            "The plan question id this speech is asking. Null for a pure "
            "follow-up or for the closing statement."
        ),
    )
    reasoning: str = Field(
        default="", description="One short internal sentence. Never spoken aloud."
    )


class CategoryScore(BaseModel):
    key: str = Field(description="Category key exactly as given in the rubric.")
    score: float = Field(description="Score on the rubric scale.")
    justification: str = Field(description="One or two sentences citing the transcript.")


class Evaluation(BaseModel):
    category_scores: list[CategoryScore]
    summary: str
    strengths: list[str]
    concerns: list[str]
    # Facts are free-form because the fact list is configuration, not code.
    facts: dict[str, float | bool | str | None] = Field(
        default_factory=dict,
        description="One entry per requested fact key. Use null when unknown.",
    )


class FactSheet(BaseModel):
    facts: dict[str, float | bool | str | None] = Field(
        default_factory=dict,
        description="One entry per requested fact key. Use null when the transcript does not establish it.",
    )


class Brain(Protocol):
    def decide_turn(self, **kwargs) -> TurnDecision: ...
    def evaluate(self, **kwargs) -> Evaluation: ...
    def extract_facts(self, **kwargs) -> dict: ...


# --------------------------------------------------------------------------- #
# Claude-backed brain
# --------------------------------------------------------------------------- #
class ClaudeBrain:
    def __init__(self, settings: Settings | None = None, client: anthropic.Anthropic | None = None):
        self.settings = settings or get_settings()
        self._client = client or anthropic.Anthropic(api_key=self.settings.anthropic_api_key)

    def decide_turn(
        self,
        *,
        kb: Knowledge,
        role: str,
        transcript: list[dict[str, str]],
        current_question: dict | None,
        next_question: dict | None,
        followups_used: int,
        max_followups: int,
        questions_remaining: int,
        seconds_remaining: int,
        forced_end_reason: str | None = None,
    ) -> TurnDecision:
        system = build_interviewer_system(kb, role)
        user = build_turn_user_message(
            transcript=transcript,
            current_question=current_question,
            next_question=next_question,
            followups_used=followups_used,
            max_followups=max_followups,
            questions_remaining=questions_remaining,
            seconds_remaining=seconds_remaining,
            forced_end_reason=forced_end_reason,
        )
        # The system prompt is per-role and byte-stable, so it caches across
        # every turn of every interview for that role. Volatile state lives in
        # the user message, after the breakpoint.
        response = self._client.messages.parse(
            model=self.settings.interview_model,
            max_tokens=2000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_format=TurnDecision,
            output_config={"effort": self.settings.interview_effort},
        )
        decision = response.parsed_output
        if decision is None:  # pragma: no cover - guarded by output_format
            raise RuntimeError("Claude returned no parsed TurnDecision")
        log.info(
            "turn decision action=%s question_id=%s cache_read=%s",
            decision.action,
            decision.question_id,
            getattr(response.usage, "cache_read_input_tokens", None),
        )
        return decision

    def extract_facts(self, *, kb: Knowledge, transcript: list[dict[str, str]]) -> dict:
        """Mid-interview fact pull, so the deterministic rules can spot a hard
        blocker and let us wrap up early instead of burning the full slot."""
        fact_lines = "\n".join(
            f"- {f.key} ({f.type}): {f.description}" for f in kb.fact_specs
        )
        convo = "\n".join(
            f"{'INTERVIEWER' if t['speaker'] == 'interviewer' else 'CANDIDATE'}: {t['text']}"
            for t in transcript
        )
        response = self._client.messages.parse(
            model=self.settings.interview_model,
            max_tokens=2000,
            system=(
                "Extract facts from a partial interview transcript. Use null for "
                "anything the transcript does not clearly establish — a guess here "
                "can end a real person's interview early. Do not infer from tone or "
                "from what is likely; only from what was said.\n\n"
                f"FACTS TO EXTRACT\n{fact_lines}"
            ),
            messages=[{"role": "user", "content": f"PARTIAL TRANSCRIPT\n\n{convo}"}],
            output_format=FactSheet,
            output_config={"effort": "low"},
        )
        sheet = response.parsed_output
        return dict(sheet.facts) if sheet else {}

    def evaluate(
        self,
        *,
        kb: Knowledge,
        role: str,
        candidate: dict,
        transcript: list[dict[str, str]],
        duration_seconds: int | None,
    ) -> Evaluation:
        response = self._client.messages.parse(
            model=self.settings.scoring_model,
            max_tokens=8000,
            system=build_scoring_system(kb, role),
            messages=[
                {
                    "role": "user",
                    "content": build_scoring_user_message(
                        candidate=candidate,
                        transcript=transcript,
                        duration_seconds=duration_seconds,
                    ),
                }
            ],
            output_format=Evaluation,
            output_config={"effort": self.settings.scoring_effort},
        )
        evaluation = response.parsed_output
        if evaluation is None:  # pragma: no cover
            raise RuntimeError("Claude returned no parsed Evaluation")
        return evaluation


# --------------------------------------------------------------------------- #
# Offline brain — no API key required
# --------------------------------------------------------------------------- #
class ScriptedBrain:
    """Walks the plan in order without following up. Used by tests, the offline
    simulator, and as a degraded fallback if ANTHROPIC_API_KEY is unset."""

    def decide_turn(
        self,
        *,
        kb: Knowledge,
        role: str,
        transcript: list[dict[str, str]],
        current_question: dict | None,
        next_question: dict | None,
        followups_used: int,
        max_followups: int,
        questions_remaining: int,
        seconds_remaining: int,
        forced_end_reason: str | None = None,
    ) -> TurnDecision:
        if forced_end_reason or next_question is None:
            _, closing = kb.script_for(role)
            return TurnDecision(
                action="END_INTERVIEW",
                speech=closing or "Thank you for your time. The hiring team will be in touch.",
                reasoning="scripted: plan complete",
            )
        return TurnDecision(
            action="NEXT_QUESTION",
            speech=str(next_question["text"]),
            question_id=str(next_question["id"]),
            reasoning="scripted: advance",
        )

    def extract_facts(self, *, kb: Knowledge, transcript: list[dict[str, str]]) -> dict:
        return {}

    def evaluate(
        self,
        *,
        kb: Knowledge,
        role: str,
        candidate: dict,
        transcript: list[dict[str, str]],
        duration_seconds: int | None,
    ) -> Evaluation:
        answered = sum(1 for t in transcript if t["speaker"] == "candidate" and t["text"].strip())
        score = min(kb.scale_max, max(0.0, answered))
        return Evaluation(
            category_scores=[
                CategoryScore(key=c.key, score=score, justification="scripted brain — no LLM call")
                for c in kb.categories
            ],
            summary=f"Scripted evaluation over {answered} candidate turns. No LLM was called.",
            strengths=[],
            concerns=["Evaluated by the scripted brain; not a real assessment."],
            facts={f.key: None for f in kb.fact_specs},
        )


def build_brain(settings: Settings | None = None) -> Brain:
    settings = settings or get_settings()
    if not settings.anthropic_api_key:
        log.warning(
            "ANTHROPIC_API_KEY is not set — falling back to the scripted brain. "
            "The interview will read questions in order and produce no real scores."
        )
        return ScriptedBrain()
    return ClaudeBrain(settings)

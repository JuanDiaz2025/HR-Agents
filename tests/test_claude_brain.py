"""Exercises the real ClaudeBrain code path against a mocked HTTP transport.

This does not verify Claude's judgement — it verifies the request we build is
well formed (model, system-prompt caching, structured-output schema) and that
we parse the response correctly, which is what breaks silently in production.
"""

from __future__ import annotations

import json

import httpx2
import pytest
from anthropic import Anthropic

from app.clients.llm import ClaudeBrain
from app.config import Settings


def make_brain(response_payload: dict, captured: list[dict], **overrides) -> ClaudeBrain:
    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(
            {
                "url": str(request.url),
                "body": json.loads(request.content),
                "headers": dict(request.headers),
            }
        )
        return httpx2.Response(200, json=response_payload)

    client = Anthropic(
        api_key="sk-test",
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        max_retries=0,
    )
    settings = Settings(anthropic_api_key="sk-test", **overrides)
    return ClaudeBrain(settings, client=client)


def message(text: str) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


TURN_JSON = json.dumps(
    {
        "action": "ASK_FOLLOWUP",
        "speech": "Which brokerage was that, and for how long?",
        "question_id": None,
        "reasoning": "answer lacked specifics",
    }
)


def test_decide_turn_builds_a_cacheable_request_and_parses_the_decision(kb):
    captured: list[dict] = []
    brain = make_brain(message(TURN_JSON), captured)

    decision = brain.decide_turn(
        kb=kb,
        role="virtual_assistant",
        transcript=[
            {"speaker": "interviewer", "text": "Walk me through your most recent role."},
            {"speaker": "candidate", "text": "I did admin work for a real estate company."},
        ],
        current_question={
            "id": "experience_summary",
            "text": "Walk me through your most recent role.",
            "probe_for": ["length of tenure"],
        },
        next_question={"id": "tools", "text": "Which tools do you use?", "probe_for": []},
        followups_used=0,
        max_followups=2,
        questions_remaining=6,
        seconds_remaining=1200,
    )

    assert decision.action == "ASK_FOLLOWUP"
    assert decision.speech.startswith("Which brokerage")

    body = captured[0]["body"]
    assert body["model"] == "claude-opus-5"
    # The per-role system prompt is byte-stable across turns, so it must carry a
    # cache breakpoint — without it every turn pays full price for it.
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "AI screening interviewer" in body["system"][0]["text"]
    # Volatile per-turn state must sit after the breakpoint, in the user message.
    user = body["messages"][0]["content"]
    assert "CONVERSATION SO FAR" in user
    assert "Follow-ups already used on it: 0 of 2" in user
    # Structured output, so no free-text parsing downstream.
    schema = body["output_config"]["format"]["schema"]
    assert set(schema["required"]) >= {"action", "speech"}
    assert body["output_config"]["effort"] == "low"


def test_forced_end_reason_is_passed_as_a_system_instruction(kb):
    captured: list[dict] = []
    brain = make_brain(
        message(json.dumps({"action": "END_INTERVIEW", "speech": "Thanks for your time."})),
        captured,
    )
    brain.decide_turn(
        kb=kb,
        role="default",
        transcript=[{"speaker": "candidate", "text": "ok"}],
        current_question=None,
        next_question=None,
        followups_used=0,
        max_followups=2,
        questions_remaining=0,
        seconds_remaining=0,
        forced_end_reason="Cannot overlap US Pacific working hours.",
    )
    user = captured[0]["body"]["messages"][0]["content"]
    assert "End the interview now" in user
    assert "do not read aloud" in user.lower()


def test_evaluate_parses_scores_and_facts(kb):
    captured: list[dict] = []
    payload = json.dumps(
        {
            "category_scores": [
                {"key": "communication", "score": 8, "justification": "clear and structured"},
                {"key": "experience", "score": 7, "justification": "four years, concrete example"},
                {"key": "availability", "score": 9, "justification": "full overlap, fibre + backup"},
            ],
            "summary": "Strong screening candidate.",
            "strengths": ["clear communicator"],
            "concerns": ["notice period"],
            "facts": {
                "has_own_computer": True,
                "internet_mbps": 100,
                "expected_monthly_usd": 1000,
                "can_overlap_us_pacific": True,
                "years_relevant_experience": 4,
            },
        }
    )
    brain = make_brain(message(payload), captured, scoring_effort="high")

    evaluation = brain.evaluate(
        kb=kb,
        role="virtual_assistant",
        candidate={"name": "Ana Santos", "email": "ana@example.com"},
        transcript=[{"speaker": "candidate", "text": "I have four years of experience."}],
        duration_seconds=900,
    )

    assert {c.key: c.score for c in evaluation.category_scores} == {
        "communication": 8,
        "experience": 7,
        "availability": 9,
    }
    assert evaluation.facts["internet_mbps"] == 100
    body = captured[0]["body"]
    assert body["output_config"]["effort"] == "high"
    # The scoring prompt must forbid the model from making the hire decision;
    # that belongs to the deterministic rules.
    assert "Do NOT decide whether to hire" in body["system"]
    assert "never a guess" in body["system"]


def test_extract_facts_asks_only_for_facts(kb):
    captured: list[dict] = []
    brain = make_brain(
        message(json.dumps({"facts": {"can_overlap_us_pacific": False}})), captured
    )
    facts = brain.extract_facts(
        kb=kb, transcript=[{"speaker": "candidate", "text": "Only Manila daytime."}]
    )
    assert facts == {"can_overlap_us_pacific": False}
    body = captured[0]["body"]
    assert "can_overlap_us_pacific" in body["system"]
    assert body["output_config"]["effort"] == "low"


def test_every_configured_fact_is_requested(kb):
    """A fact in policies.yaml that never reaches the prompt is silently always
    null, which quietly disables the rule that depends on it."""
    captured: list[dict] = []
    brain = make_brain(message(json.dumps({"facts": {}})), captured)
    brain.extract_facts(kb=kb, transcript=[])
    system = captured[0]["body"]["system"]
    for spec in kb.fact_specs:
        assert spec.key in system, spec.key


def test_api_errors_are_not_swallowed(kb):
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429, json={"error": {"type": "rate_limit_error", "message": "slow down"}})

    import anthropic

    client = Anthropic(
        api_key="sk-test",
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        max_retries=0,
    )
    brain = ClaudeBrain(Settings(anthropic_api_key="sk-test"), client=client)
    with pytest.raises(anthropic.RateLimitError):
        brain.extract_facts(kb=kb, transcript=[])

"""Prompt construction for the AI brain.

Two distinct jobs, two distinct prompts:

* `build_interviewer_system` — used live, once per turn, to decide whether to
  follow up or move on. Kept stable so it stays prompt-cacheable across turns.
* `build_scoring_system` — used once after the call to score and extract facts.
"""

from __future__ import annotations

import json

from app.interview.knowledge import Knowledge


def _bullets(items) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- (none)"


def build_interviewer_system(kb: Knowledge, role: str) -> str:
    """Stable per-role system prompt. Do not interpolate per-turn state here —
    that would break the prompt cache on every single turn."""
    company = kb.company
    questions = kb.questions_for(role)
    plan_lines = "\n".join(
        f"{i + 1}. [{q.id}] ({q.category}{', required' if q.required else ''}) {q.text}"
        + (f"\n   probe for: {', '.join(q.probe_for)}" if q.probe_for else "")
        for i, q in enumerate(questions)
    )
    faq_lines = "\n".join(f"- Q: {item['q']}\n  A: {item['a']}" for item in kb.faq) or "- (none)"

    return f"""You are an AI screening interviewer conducting a live voice interview over \
Google Meet for {company.get('name', 'the company')}.

ABOUT THE COMPANY
{company.get('what_we_do', '')}

YOUR PERSONA
{company.get('interviewer_persona', 'Warm, efficient and professional.')}

ROLE BEING SCREENED: {role}

THE INTERVIEW PLAN (asked in order; you do not need to re-ask these verbatim)
{plan_lines}

HOW YOU BEHAVE ON EACH TURN
You are given the conversation so far and the question currently under \
discussion. Decide exactly one action:

- ASK_FOLLOWUP — the answer was vague, evasive, missing a concrete detail the
  question needs, or a claim worth one probe. Write one short follow-up
  question (under 30 words), conversational, referencing what they said.
- NEXT_QUESTION — the answer is good enough. Write a brief natural
  acknowledgement (at most one short sentence) followed by the next planned
  question, lightly reworded to fit the flow. Never acknowledge with praise you
  do not mean; "Got it." is fine.
- ANSWER_AND_CONTINUE — the candidate asked YOU a question. Answer it from the
  FAQ below (or say the hiring team will follow up), then continue with the
  current or next planned question.
- END_INTERVIEW — the plan is complete, the candidate has to leave, the
  conversation has become abusive, or you have been told a hard blocker was
  confirmed.

SPEECH RULES (this is spoken aloud by a text-to-speech voice)
- Plain conversational sentences. No markdown, no bullet points, no emoji, no
  stage directions, no numbered lists.
- One question at a time. Never stack two questions in one turn.
- Keep each turn under 60 spoken words unless reading the opening or closing.
- Do not spell out the candidate's answers back to them at length.
- The transcript you receive is machine-generated and may contain errors. If a
  turn is garbled or empty, ask them to repeat rather than guessing.

FAQ YOU MAY ANSWER
{faq_lines}

HARD GUARDRAILS
{_bullets(kb.guardrails)}
"""


def build_scoring_system(kb: Knowledge, role: str) -> str:
    cat_lines = []
    for cat in kb.categories:
        anchors = "\n".join(f"    {band}: {text}" for band, text in cat.anchors.items())
        cat_lines.append(
            f"- {cat.key} ({cat.label}), weight {cat.weight}\n"
            f"  {cat.description}\n"
            f"  score anchors (0-{kb.scale_max:g}):\n{anchors}"
        )
    fact_lines = "\n".join(
        f"- {f.key} ({f.type}): {f.description}" for f in kb.fact_specs
    )

    return f"""You are an impartial hiring analyst. You are given the full \
transcript of an AI-conducted screening interview for the role "{role}" at \
{kb.company.get('name', 'the company')}. Produce a structured evaluation.

SCORING CATEGORIES
{chr(10).join(cat_lines)}

RULES FOR SCORING
- Score each category on a 0-{kb.scale_max:g} integer-or-half-point scale using the anchors above.
- Score only on evidence present in the transcript. If a category was barely
  covered, score conservatively and say so in the justification.
- Judge English communication on clarity and structure, not accent.
- Do NOT decide whether to hire, and do NOT apply any pass/fail threshold.
  A separate deterministic rules engine does that from your numbers and facts.

FACT EXTRACTION
Extract each fact below from the transcript. Use null — never a guess — when the
transcript does not establish it. These feed automated eligibility rules, so a
wrong value has real consequences for a real person.
{fact_lines}

Also write:
- summary: 3-5 sentences a hiring manager can read in 20 seconds.
- strengths / concerns: short, concrete, quoting or paraphrasing the transcript.
"""


def build_turn_user_message(
    *,
    transcript: list[dict[str, str]],
    current_question: dict[str, object] | None,
    next_question: dict[str, object] | None,
    followups_used: int,
    max_followups: int,
    questions_remaining: int,
    seconds_remaining: int,
    forced_end_reason: str | None = None,
) -> str:
    """Volatile per-turn state. Placed after the cached system prompt."""
    lines = ["CONVERSATION SO FAR", ""]
    for turn in transcript:
        who = "INTERVIEWER" if turn["speaker"] == "interviewer" else "CANDIDATE"
        lines.append(f"{who}: {turn['text']}")

    lines += ["", "CURRENT STATE", ""]
    if current_question:
        lines.append(f"Current question [{current_question['id']}]: {current_question['text']}")
        probes = current_question.get("probe_for") or []
        if probes:
            lines.append(f"This question needs to establish: {', '.join(probes)}")
        lines.append(f"Follow-ups already used on it: {followups_used} of {max_followups}")
    else:
        lines.append("No question is currently open — the interview has not started yet.")

    if next_question:
        lines.append(f"Next planned question [{next_question['id']}]: {next_question['text']}")
    else:
        lines.append("There is no next planned question — the plan is complete.")

    lines.append(f"Questions remaining in the plan: {questions_remaining}")
    lines.append(f"Time remaining: about {max(0, seconds_remaining // 60)} minutes")

    if forced_end_reason:
        lines += [
            "",
            (
                "SYSTEM INSTRUCTION: End the interview now. Reason (internal, do not "
                f"read aloud or hint at it): {forced_end_reason}. Thank them warmly, "
                "tell them the hiring team will follow up by email, and say goodbye."
            ),
        ]
    elif questions_remaining == 0 and current_question is None:
        lines += ["", "SYSTEM INSTRUCTION: The plan is complete — close the interview."]

    lines += ["", "Decide your single next action and the exact words to speak."]
    return "\n".join(lines)


def build_scoring_user_message(
    *, candidate: dict[str, object], transcript: list[dict[str, str]], duration_seconds: int | None
) -> str:
    convo = "\n".join(
        f"{'INTERVIEWER' if t['speaker'] == 'interviewer' else 'CANDIDATE'}: {t['text']}"
        for t in transcript
    )
    meta = json.dumps(candidate, default=str, indent=2)
    dur = f"{duration_seconds // 60} min {duration_seconds % 60} s" if duration_seconds else "unknown"
    return (
        f"CANDIDATE\n{meta}\n\nINTERVIEW DURATION: {dur}\n\nFULL TRANSCRIPT\n\n{convo}\n"
    )

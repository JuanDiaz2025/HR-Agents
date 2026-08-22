from __future__ import annotations

import asyncio

from app.clients.llm import TurnDecision
from app.db import SessionLocal
from app.interview.engine import InterviewEngine
from app.models import Interview, InterviewStatus, Speaker


def build_engine(kb, voice, brain, on_finished=None, **overrides):
    from app.config import Settings

    settings = Settings(**overrides) if overrides else Settings()
    return InterviewEngine(
        brain=brain, voice=voice, knowledge=kb, settings=settings, on_finished=on_finished
    )


def load(interview_id: str) -> Interview:
    with SessionLocal() as session:
        interview = session.get(Interview, interview_id)
        assert interview is not None
        _ = [t.text for t in interview.turns]
        return interview


async def test_start_greets_and_marks_in_progress(kb, voice, fake_brain, make_interview):
    iv = make_interview()
    engine = build_engine(kb, voice, fake_brain())
    await engine.start(iv)

    assert len(voice.utterances) == 1
    assert "Ana" in voice.utterances[0]  # opening template is personalised
    assert "{first_name}" not in voice.utterances[0]
    interview = load(iv)
    assert interview.status is InterviewStatus.in_progress
    assert interview.started_at is not None
    assert interview.question_plan  # plan was frozen onto the row


async def test_start_is_idempotent(kb, voice, fake_brain, make_interview):
    iv = make_interview()
    engine = build_engine(kb, voice, fake_brain())
    await engine.start(iv)
    await engine.start(iv)
    assert len(voice.utterances) == 1


async def test_answer_advances_the_plan(kb, voice, fake_brain, make_interview):
    iv = make_interview()
    engine = build_engine(kb, voice, fake_brain())
    await engine.start(iv)

    await engine.on_utterance(iv, text="Yes, I'm ready.", speaker_name="Ana Santos")
    await engine.advance(iv)

    interview = load(iv)
    assert interview.plan_cursor == 1
    assert interview.questions_asked == 1
    texts = [t.text for t in interview.turns]
    assert "Yes, I'm ready." in texts
    # The candidate turn is recorded as the candidate, not the interviewer.
    assert any(t.speaker is Speaker.candidate for t in interview.turns)


async def test_followups_are_capped_then_the_plan_advances(kb, voice, fake_brain, make_interview):
    iv = make_interview()
    # Three consecutive follow-up decisions, but the cap is 2.
    brain = fake_brain(
        decisions=[
            TurnDecision(action="NEXT_QUESTION", speech="Tell me about yourself.", question_id="intro"),
            TurnDecision(action="ASK_FOLLOWUP", speech="Can you be more specific?"),
            TurnDecision(action="ASK_FOLLOWUP", speech="And which company was that?"),
            TurnDecision(action="ASK_FOLLOWUP", speech="Once more, please?"),
        ]
    )
    engine = build_engine(kb, voice, brain)
    await engine.start(iv)
    for text in ["Ready.", "I do stuff.", "Various things.", "You know, things."]:
        await engine.on_utterance(iv, text=text)
        await engine.advance(iv)

    interview = load(iv)
    assert interview.followups_used == 0  # reset by the forced advance
    assert interview.plan_cursor == 2  # intro asked, then forced on to the next
    assert "Once more, please?" not in voice.utterances


async def test_own_speech_echo_is_not_treated_as_an_answer(kb, voice, fake_brain, make_interview):
    iv = make_interview()
    engine = build_engine(kb, voice, fake_brain())
    await engine.start(iv)
    # The transcriber picks up a chunk of the opening we are still speaking.
    echo = " ".join(voice.utterances[0].split()[:8])
    await engine.on_utterance(iv, text=echo)
    assert engine.runtime(iv).buffer == []


async def test_candidate_talking_over_the_bot_is_still_heard(kb, voice, fake_brain, make_interview):
    """Barge-in matters: a candidate who interrupts must not be discarded."""
    iv = make_interview()
    engine = build_engine(kb, voice, fake_brain())
    await engine.start(iv)
    await engine.on_utterance(iv, text="Sorry, could you repeat the question?")
    assert engine.runtime(iv).buffer == ["Sorry, could you repeat the question?"]


async def test_bot_named_speaker_is_ignored(kb, voice, fake_brain, make_interview):
    iv = make_interview()
    engine = build_engine(kb, voice, fake_brain())
    await engine.start(iv)
    await engine.on_utterance(iv, text="Something entirely different", speaker_name="AI Interviewer")
    assert engine.runtime(iv).buffer == []


async def test_debounce_batches_fragments_into_one_answer(kb, voice, fake_brain, make_interview):
    iv = make_interview()
    engine = build_engine(kb, voice, fake_brain(), answer_debounce_s=0.05)
    await engine.start(iv)

    for fragment in ["I worked at", "a real estate firm", "for three years."]:
        await engine.on_utterance(iv, text=fragment)
        await asyncio.sleep(0.01)  # faster than the debounce window
    await asyncio.sleep(0.15)  # now let it fire

    interview = load(iv)
    candidate_turns = [t.text for t in interview.turns if t.speaker is Speaker.candidate]
    assert candidate_turns == ["I worked at a real estate firm for three years."]


async def test_end_interview_finishes_and_calls_the_pipeline(kb, voice, fake_brain, make_interview):
    iv = make_interview()
    finished: list[str] = []

    async def on_finished(interview_id: str) -> None:
        finished.append(interview_id)

    brain = fake_brain(decisions=[TurnDecision(action="END_INTERVIEW", speech="Thanks, goodbye.")])
    engine = build_engine(kb, voice, brain, on_finished=on_finished)
    await engine.start(iv)
    await engine.on_utterance(iv, text="I have to go.")
    await engine.advance(iv)

    assert finished == [iv]
    interview = load(iv)
    assert interview.status is InterviewStatus.completed
    assert interview.ended_at is not None
    assert "goodbye" in voice.utterances[-1].lower()


async def test_time_limit_forces_the_close(kb, voice, fake_brain, make_interview):
    iv = make_interview()
    brain = fake_brain()
    engine = build_engine(kb, voice, brain, max_interview_seconds=0)
    await engine.start(iv)
    await engine.on_utterance(iv, text="Ready.")
    decision = await engine.advance(iv)

    assert decision is not None and decision.action == "END_INTERVIEW"
    assert brain.turn_calls[-1]["forced_end_reason"] == "time limit reached"
    assert load(iv).status is InterviewStatus.completed


async def test_question_limit_forces_the_close(kb, voice, fake_brain, make_interview):
    iv = make_interview()
    brain = fake_brain()
    engine = build_engine(kb, voice, brain, max_questions=1)
    await engine.start(iv)
    for text in ["Ready.", "My background is in admin work."]:
        await engine.on_utterance(iv, text=text)
        await engine.advance(iv)
    assert load(iv).status is InterviewStatus.completed
    assert brain.turn_calls[-1]["forced_end_reason"] == "question limit reached"


async def test_full_plan_runs_to_completion(kb, voice, fake_brain, make_interview):
    iv = make_interview(role="virtual_assistant")
    engine = build_engine(kb, voice, fake_brain())
    await engine.start(iv)

    plan_length = len(kb.questions_for("virtual_assistant"))
    for i in range(plan_length + 2):
        interview = load(iv)
        if interview.status is not InterviewStatus.in_progress:
            break
        await engine.on_utterance(iv, text=f"Answer number {i}.")
        await engine.advance(iv)

    interview = load(iv)
    assert interview.status is InterviewStatus.completed
    assert interview.questions_asked == plan_length
    assert interview.plan_cursor == plan_length


async def test_no_show_is_recorded_when_nobody_speaks(kb, voice, fake_brain, make_interview):
    iv = make_interview()
    engine = build_engine(kb, voice, fake_brain())
    await engine.start(iv)
    await engine.finish(iv, reason="everyone_left")

    interview = load(iv)
    assert interview.status is InterviewStatus.no_show
    assert interview.end_reason == "everyone_left"


async def test_finish_is_idempotent(kb, voice, fake_brain, make_interview):
    iv = make_interview()
    calls: list[str] = []

    async def on_finished(interview_id: str) -> None:
        calls.append(interview_id)

    engine = build_engine(kb, voice, fake_brain(), on_finished=on_finished)
    await engine.start(iv)
    await engine.finish(iv, reason="first")
    await engine.finish(iv, reason="second")
    assert len(calls) == 1
    assert load(iv).end_reason == "first"


async def test_fail_records_the_error(kb, voice, fake_brain, make_interview):
    iv = make_interview()
    engine = build_engine(kb, voice, fake_brain())
    await engine.fail(iv, error="meeting_link_invalid")
    interview = load(iv)
    assert interview.status is InterviewStatus.failed
    assert "meeting_link_invalid" in (interview.error or "")


async def test_utterance_for_unknown_interview_is_harmless(kb, voice, fake_brain):
    engine = build_engine(kb, voice, fake_brain())
    await engine.on_utterance("missing", text="hello?")
    assert await engine.advance("missing") is None


async def test_early_exit_blocker_ends_the_interview(kb, voice, fake_brain, make_interview):
    """A hard blocker confirmed mid-call should close the interview on the next
    turn rather than running the full plan."""
    iv = make_interview()
    brain = fake_brain(facts={"can_overlap_us_pacific": False})
    engine = build_engine(kb, voice, brain, early_exit_check_categories=["communication"])
    await engine.start(iv)

    # Turn 1 opens `intro`; no question has completed yet, so no facts pulled.
    await engine.on_utterance(iv, text="Ready when you are.")
    await engine.advance(iv)
    assert load(iv).status is InterviewStatus.in_progress

    # Turn 2 completes `intro` (a communication question), the facts are pulled,
    # the blocker trips, and the interview closes on this same turn.
    await engine.on_utterance(iv, text="I can only work Manila daytime hours.")
    await engine.advance(iv)

    interview = load(iv)
    assert interview.status is InterviewStatus.completed
    assert "overlap" in (interview.end_reason or "")
    # It closed early rather than working through the whole plan.
    assert interview.questions_asked < len(kb.questions_for("default"))


async def test_empty_utterances_are_dropped(kb, voice, fake_brain, make_interview):
    iv = make_interview()
    engine = build_engine(kb, voice, fake_brain())
    await engine.start(iv)
    await engine.on_utterance(iv, text="   ")
    assert engine.runtime(iv).buffer == []


async def test_answer_still_in_the_buffer_is_saved_when_the_call_ends(
    kb, voice, fake_brain, make_interview
):
    """A candidate who answers and then hangs up must not be recorded as a
    no-show — their last words are still in the debounce buffer."""
    iv = make_interview()
    engine = build_engine(kb, voice, fake_brain(), answer_debounce_s=30)
    await engine.start(iv)
    await engine.on_utterance(iv, text="Yes, I have four years of experience.")
    # Call drops before the debounce window elapses.
    await engine.finish(iv, reason="everyone_left")

    interview = load(iv)
    assert interview.status is InterviewStatus.completed
    texts = [t.text for t in interview.turns if t.speaker is Speaker.candidate]
    assert texts == ["Yes, I have four years of experience."]


async def test_no_script_placeholder_ever_reaches_the_candidate(
    kb, voice, fake_brain, make_interview
):
    """A leaked "{first_name}" is spoken out loud to a real person, so every
    utterance — opening, closing, and anything the model echoes — is filled."""
    iv = make_interview(name="Ana Santos")
    # Have the brain hand back the raw closing template, placeholders included,
    # exactly as a model echoing the script would.
    raw_closing = kb.script_for("default")[1]
    assert "{first_name}" in raw_closing
    brain = fake_brain(decisions=[TurnDecision(action="END_INTERVIEW", speech=raw_closing)])
    engine = build_engine(kb, voice, brain)

    await engine.start(iv)
    await engine.on_utterance(iv, text="I need to go, sorry.")
    await engine.advance(iv)

    assert load(iv).status is InterviewStatus.completed
    assert voice.utterances[0].startswith("Hi Ana,")
    assert voice.utterances[-1].startswith("That's everything I needed, Ana.")
    for utterance in voice.utterances:
        assert "{" not in utterance, utterance


async def test_unknown_placeholder_is_left_alone_not_crashed(kb, voice, fake_brain, make_interview):
    iv = make_interview()
    brain = fake_brain(
        decisions=[TurnDecision(action="ASK_FOLLOWUP", speech="What about {unknown_thing}?")]
    )
    engine = build_engine(kb, voice, brain)
    await engine.start(iv)
    await engine.on_utterance(iv, text="Ready.")
    await engine.advance(iv)
    assert "{unknown_thing}" in voice.utterances[-1]

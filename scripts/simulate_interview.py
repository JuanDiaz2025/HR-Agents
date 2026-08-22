#!/usr/bin/env python
"""Run a full interview end to end, locally, with no external accounts.

    python scripts/simulate_interview.py                 # scripted answers
    python scripts/simulate_interview.py --interactive    # you play the candidate
    python scripts/simulate_interview.py --scenario weak
    python scripts/simulate_interview.py --role virtual_assistant

With ANTHROPIC_API_KEY set, the real Claude brain drives follow-ups and
scoring. Without it, the scripted brain walks the plan in order. Either way no
meeting, no bot and no Google/monday.com credentials are involved — the voice
adapter just prints, and the sinks log what they would have written.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///./simulate.db")

from app.clients.llm import build_brain
from app.clients.monday import build_monday_sink
from app.clients.notify import Notifier
from app.clients.sheets import build_sheets_sink
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.interview.engine import InterviewEngine
from app.interview.knowledge import cached_knowledge
from app.logging_config import configure_logging
from app.models import Candidate, Interview, InterviewStatus
from app.services.pipeline import ResultsPipeline

# Answer scripts. Keys are matched against the question id the AI just asked;
# `_default` covers anything unmatched (including follow-ups).
#
# `_facts` and `_scores` are what a real LLM would have extracted and scored for
# these answers. Without an API key the simulator feeds them to the *real*
# scoring and rules engine, so the deterministic half of the system — weights,
# thresholds, disqualification, publication — is genuinely exercised offline.
# They are hand-written demo data, not a judgement made by any model.
SCENARIOS: dict[str, dict[str, str]] = {
    "strong": {
        "_opening": "Yes, I'm ready — thanks for having me.",
        "intro": (
            "Sure. I'm Ana, I've been doing remote admin and transaction coordination "
            "for US real estate teams for about four years. I'm looking for a full-time "
            "remote role where I can own a process end to end."
        ),
        "va_written_english": (
            "I'd say advanced. I write all the client-facing emails on my current team. "
            "I keep a template library and I read everything back once before sending."
        ),
        "experience_summary": (
            "For the last two and a half years I was a transaction coordinator at a "
            "brokerage in Texas. Day to day I managed about twenty five active files, "
            "chased signatures in DocuSign, kept the deal calendar, and sent the weekly "
            "status email to agents."
        ),
        "relevant_example": (
            "We nearly lost a closing because an inspection addendum was never "
            "countersigned. I noticed the gap in my own tracker two days before the "
            "deadline, got the agent on the phone, and had it signed the same afternoon. "
            "After that I built a checklist step for it and we didn't miss one again."
        ),
        "tools": "DocuSign, Dotloop, Monday.com, Google Workspace, and Slack every day.",
        "va_admin_tools": (
            "Monday.com, yes. I ran our deal pipeline board — one item per property, "
            "columns for status, closing date and lender, and I set up an automation "
            "that pinged the agent when a file sat in one status for five days."
        ),
        "availability_hours": (
            "I can work eight to five Pacific time, which is late night here in Manila. "
            "I've been on that schedule for two years so it's normal for me."
        ),
        "start_date": "I could start in two weeks — I want to give my current team notice.",
        "work_setup": (
            "I have my own laptop, a Ryzen machine I bought last year. Fiber at home, "
            "about a hundred megabits, and I have mobile data as backup plus a small UPS "
            "for the router. I work from a spare room with the door closed, so it's quiet."
        ),
        "compensation": "I'm looking for around one thousand US dollars a month.",
        "candidate_questions": "Just — how soon would the team want someone to start?",
        "_default": "Yes, that's right. Happy to go into more detail if useful.",
        "_scores": {"communication": 9, "experience": 8, "availability": 9},
        "_facts": {
            "has_own_computer": True,
            "quiet_workspace": True,
            "has_backup_internet": True,
            "internet_mbps": 100,
            "weekly_hours_available": 40,
            "can_overlap_us_pacific": True,
            "earliest_start_days": 14,
            "expected_monthly_usd": 1000,
            "years_relevant_experience": 4,
            "currently_employed_elsewhere_fulltime": False,
        },
    },
    "weak": {
        "_opening": "Yeah okay.",
        "intro": "I'm looking for any online job. I can do many things.",
        "experience_summary": "I worked in a call center for a few months. It was okay.",
        "relevant_example": "I helped customers. Normal problems, nothing special.",
        "tools": "Facebook, and Microsoft Word.",
        "availability_hours": (
            "I can only work in the morning here, Manila time. Night shift is hard for me."
        ),
        "start_date": "Anytime, I'm free.",
        "work_setup": (
            "I use the computer shop near my house. My phone data is not so fast, "
            "maybe five megabits. My house is a bit noisy, there are children."
        ),
        "compensation": "Two thousand five hundred dollars.",
        "_default": "Um, I think so, yes.",
        "_scores": {"communication": 4, "experience": 3, "availability": 2},
        "_facts": {
            "has_own_computer": False,
            "quiet_workspace": False,
            "has_backup_internet": False,
            "internet_mbps": 5,
            "weekly_hours_available": 20,
            "can_overlap_us_pacific": False,
            "earliest_start_days": 0,
            "expected_monthly_usd": 2500,
            "years_relevant_experience": 0.5,
            "currently_employed_elsewhere_fulltime": False,
        },
    },
    "disqualified": {
        "_opening": "Sure.",
        "intro": "I'm a fresh graduate looking for remote work.",
        "experience_summary": "No work experience yet, just my internship.",
        "availability_hours": (
            "I can't do US hours, I have classes in the evening. Only Manila daytime."
        ),
        "work_setup": "I don't have a computer, I would use my phone.",
        "_default": "Yes.",
        "_scores": {"communication": 6, "experience": 2, "availability": 1},
        "_facts": {
            "has_own_computer": False,
            "can_overlap_us_pacific": False,
            "weekly_hours_available": 15,
            "years_relevant_experience": 0,
        },
    },
}


class ScenarioBrain:
    """Offline stand-in for Claude.

    Walks the question plan in order (no follow-ups — that judgement needs a
    model) but returns the scenario's hand-written facts and scores, so the
    deterministic scoring, rules and publication code all run for real.
    """

    def __init__(self, scenario: dict):
        self.facts = dict(scenario.get("_facts", {}))
        self.scores = dict(scenario.get("_scores", {}))

    def decide_turn(self, *, kb, role, next_question, forced_end_reason=None, **_):
        from app.clients.llm import TurnDecision

        if forced_end_reason or next_question is None:
            return TurnDecision(
                action="END_INTERVIEW", speech=kb.script_for(role)[1], reasoning="plan complete"
            )
        return TurnDecision(
            action="NEXT_QUESTION",
            speech=str(next_question["text"]),
            question_id=str(next_question["id"]),
            reasoning="scenario playback",
        )

    def extract_facts(self, **_):
        return dict(self.facts)

    def evaluate(self, *, kb, **_):
        from app.clients.llm import CategoryScore, Evaluation

        return Evaluation(
            category_scores=[
                CategoryScore(
                    key=c.key,
                    score=float(self.scores.get(c.key, 5)),
                    justification="scenario playback — not a model judgement",
                )
                for c in kb.categories
            ],
            summary="Offline scenario playback: scores and facts are scripted demo data.",
            strengths=[],
            concerns=["Offline simulation — no model evaluated this transcript."],
            facts=dict(self.facts),
        )


class PrintingVoice:
    """Prints what the bot would say. Substitutes for TTS + Recall output_audio."""

    def __init__(self) -> None:
        self.utterances: list[str] = []

    async def speak(self, bot_id: str | None, text: str) -> None:
        self.utterances.append(text)
        print(f"\n\033[96m🤖 INTERVIEWER:\033[0m {text}")


def seed_interview(name: str, email: str, role: str) -> str:
    with SessionLocal() as session:
        candidate = Candidate(full_name=name, email=email, role_applied=role, source="simulator")
        session.add(candidate)
        session.flush()
        interview = Interview(
            candidate_id=candidate.id,
            scheduled_at=datetime.now(UTC) + timedelta(minutes=1),
            status=InterviewStatus.scheduled,
            bot_id="simulated-bot",
            meeting_url="https://meet.google.com/simulated",
        )
        session.add(interview)
        session.commit()
        return interview.id


def next_answer(script: dict[str, str], last_question_id: str | None, turn: int) -> str:
    if turn == 0:
        return script.get("_opening", "Yes, ready.")
    if last_question_id and last_question_id in script:
        return script[last_question_id]
    return script.get("_default", "Yes.")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="strong")
    parser.add_argument("--role", default="virtual_assistant")
    parser.add_argument("--name", default="Ana Santos")
    parser.add_argument("--email", default="ana.santos@example.com")
    parser.add_argument("--interactive", action="store_true", help="type the answers yourself")
    parser.add_argument("--max-turns", type=int, default=40)
    args = parser.parse_args()

    configure_logging("WARNING")
    settings = get_settings()
    init_db()

    kb = cached_knowledge()
    script = SCENARIOS[args.scenario]
    if settings.anthropic_api_key:
        brain = build_brain(settings)
        brain_note = f"{type(brain).__name__} (live Claude)"
    else:
        brain = ScenarioBrain(script)
        brain_note = "ScenarioBrain (offline — scores are scripted demo data)"
    voice = PrintingVoice()
    pipeline = ResultsPipeline(
        brain=brain,
        sheets=build_sheets_sink(settings),
        monday=build_monday_sink(settings),
        notifier=Notifier(settings),
        knowledge=kb,
    )
    engine = InterviewEngine(
        brain=brain, voice=voice, knowledge=kb, settings=settings, on_finished=pipeline.run
    )

    interview_id = seed_interview(args.name, args.email, args.role)

    print("=" * 78)
    print(f"  SIMULATED INTERVIEW — {args.name} · role={args.role}")
    print(f"  Brain: {brain_note}")
    if args.interactive:
        print("  You are the candidate. Type your answers; Ctrl-C to abort.")
        if not settings.anthropic_api_key:
            print("  NOTE: without ANTHROPIC_API_KEY the score below is the scenario's")
            print("        scripted demo data, not an evaluation of what you actually say.")
    else:
        print(f"  Scenario: {args.scenario}")
    print("=" * 78)

    await engine.start(interview_id)

    last_question_id: str | None = None
    for turn in range(args.max_turns):
        with SessionLocal() as session:
            interview = session.get(Interview, interview_id)
            if interview is None or interview.status is not InterviewStatus.in_progress:
                break
            cursor = interview.plan_cursor
            plan = interview.question_plan
            last_question_id = plan[cursor - 1] if 0 < cursor <= len(plan) else None

        if args.interactive:
            try:
                answer = input("\n\033[93m🧑 YOU:\033[0m ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not answer:
                continue
        else:
            answer = next_answer(script, last_question_id, turn)
            print(f"\n\033[93m🧑 {args.name.upper()}:\033[0m {answer}")

        # Bypass the silence debounce — feed the utterance and advance at once.
        engine.runtime(interview_id).speaking_until = 0
        await engine.on_utterance(interview_id, text=answer, speaker_name=args.name)
        await engine.advance(interview_id)

    with SessionLocal() as session:
        interview = session.get(Interview, interview_id)
        if interview is not None and interview.status is InterviewStatus.in_progress:
            await engine.finish(interview_id, reason="simulator turn limit")

    # ----------------------------------------------------------------- results
    with SessionLocal() as session:
        interview = session.get(Interview, interview_id)
        assert interview is not None
        sc = interview.scorecard

        print("\n" + "=" * 78)
        print("  RESULT")
        print("=" * 78)
        print(f"  Status          : {interview.status.value}  ({interview.end_reason})")
        print(f"  Questions asked : {interview.questions_asked}")
        print(f"  Transcript turns: {len(interview.turns)}")
        if sc is None:
            print("  No scorecard was produced.")
        else:
            print(f"  Overall score   : {sc.overall_score}/100")
            print(f"  Recommendation  : {sc.recommendation.value}")
            for row in sc.category_scores:
                print(f"    - {row['label']:<22} {row['score']}/{row['max']:g}  {row.get('justification', '')}")
            if sc.disqualified:
                print("  DISQUALIFIED:")
                for reason in sc.disqualification_reasons:
                    print(f"    ✗ {reason}")
            print(f"\n  Summary: {sc.summary}")
            if sc.strengths:
                print("  Strengths: " + "; ".join(sc.strengths))
            if sc.concerns:
                print("  Concerns : " + "; ".join(sc.concerns))
            facts = {k: v for k, v in (sc.facts or {}).items() if v is not None}
            if facts:
                print(f"  Facts extracted: {facts}")
        print(f"\n  Interview id: {interview.id}")
        print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

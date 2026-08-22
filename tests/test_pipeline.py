from __future__ import annotations

from app.clients.llm import CategoryScore, Evaluation
from app.clients.monday import NullMondaySink
from app.clients.notify import Notifier
from app.clients.sheets import HEADER, NullSheetsSink, row_to_values
from app.db import SessionLocal
from app.models import Interview, InterviewStatus, Recommendation, Speaker, TranscriptTurn
from app.services.pipeline import ResultsPipeline


class RecordingSheets(NullSheetsSink):
    def __init__(self):
        self.rows: list[dict] = []

    def upsert_row(self, row):
        self.rows.append(row)
        return len(self.rows) + 1


class RecordingMonday(NullMondaySink):
    def __init__(self):
        self.calls: list[dict] = []

    async def upsert_candidate(self, **kwargs):
        self.calls.append(kwargs)
        return "item-42"


def build_pipeline(kb, brain):
    return ResultsPipeline(
        brain=brain,
        sheets=RecordingSheets(),
        monday=RecordingMonday(),
        notifier=Notifier(),
        knowledge=kb,
    )


def add_turns(interview_id: str, pairs: list[tuple[str, str]]) -> None:
    with SessionLocal() as session:
        for i, (speaker, text) in enumerate(pairs, start=1):
            session.add(
                TranscriptTurn(
                    interview_id=interview_id,
                    sequence=i,
                    speaker=Speaker(speaker),
                    text=text,
                )
            )
        session.commit()


def mark(interview_id: str, status: InterviewStatus) -> None:
    from app.models import utcnow

    with SessionLocal() as session:
        interview = session.get(Interview, interview_id)
        interview.status = status
        interview.started_at = interview.started_at or utcnow()
        interview.ended_at = utcnow()
        session.commit()


async def test_scores_and_publishes_a_completed_interview(kb, fake_brain, make_interview):
    iv = make_interview()
    add_turns(iv, [("interviewer", "Tell me about yourself."), ("candidate", "I have four years in admin.")])
    mark(iv, InterviewStatus.completed)

    pipeline = build_pipeline(kb, fake_brain(facts={"has_own_computer": True}))
    await pipeline.run(iv)

    with SessionLocal() as session:
        sc = session.get(Interview, iv).scorecard
        assert sc is not None
        assert sc.overall_score == 80.0
        assert sc.recommendation is Recommendation.proceed
        assert sc.monday_item_id == "item-42"
        assert sc.sheet_row is not None
        assert sc.published_at is not None
        # Justifications are merged onto the deterministic rows.
        assert all("justification" in row for row in sc.category_scores)


async def test_disqualifying_fact_overrides_good_scores(kb, fake_brain, make_interview):
    iv = make_interview()
    add_turns(iv, [("interviewer", "Your setup?"), ("candidate", "I use an internet cafe PC.")])
    mark(iv, InterviewStatus.completed)

    brain = fake_brain(facts={"has_own_computer": False})
    pipeline = build_pipeline(kb, brain)
    await pipeline.run(iv)

    with SessionLocal() as session:
        sc = session.get(Interview, iv).scorecard
        assert sc.recommendation is Recommendation.do_not_proceed
        assert sc.disqualified
        assert sc.overall_score == 80.0  # the score itself is not falsified
    assert pipeline.monday.calls[-1]["recommendation"] is Recommendation.do_not_proceed


async def test_no_show_is_recorded_without_calling_the_llm(kb, fake_brain, make_interview):
    iv = make_interview()
    mark(iv, InterviewStatus.no_show)
    brain = fake_brain()
    pipeline = build_pipeline(kb, brain)
    await pipeline.run(iv)

    assert brain.evaluate_calls == []  # nothing to score, so no spend
    with SessionLocal() as session:
        sc = session.get(Interview, iv).scorecard
        assert sc.recommendation is Recommendation.do_not_proceed
        assert sc.disqualified
        assert "did not attend" in sc.disqualification_reasons[0].lower()


async def test_transcript_with_no_candidate_speech_routes_to_review(kb, fake_brain, make_interview):
    iv = make_interview()
    add_turns(iv, [("interviewer", "Hello? Can you hear me?")])
    mark(iv, InterviewStatus.completed)
    pipeline = build_pipeline(kb, fake_brain())
    await pipeline.run(iv)

    with SessionLocal() as session:
        sc = session.get(Interview, iv).scorecard
        assert sc.recommendation is Recommendation.review
        assert not sc.disqualified


async def test_rerunning_updates_rather_than_duplicating(kb, fake_brain, make_interview):
    iv = make_interview()
    add_turns(iv, [("interviewer", "Q"), ("candidate", "A")])
    mark(iv, InterviewStatus.completed)
    pipeline = build_pipeline(kb, fake_brain())
    await pipeline.run(iv)
    await pipeline.run(iv)

    with SessionLocal() as session:
        from sqlalchemy import func, select

        from app.models import Scorecard

        assert session.scalar(select(func.count()).select_from(Scorecard)) == 1


async def test_review_recommendation_emails_a_human(kb, fake_brain, make_interview):
    iv = make_interview()
    add_turns(iv, [("interviewer", "Q"), ("candidate", "A")])
    mark(iv, InterviewStatus.completed)

    borderline = Evaluation(
        category_scores=[CategoryScore(key=c.key, score=6.0, justification="ok") for c in kb.categories],
        summary="Borderline candidate.",
        strengths=[],
        concerns=["thin examples"],
        facts={},
    )
    pipeline = build_pipeline(kb, fake_brain(evaluation=borderline))
    await pipeline.run(iv)

    channels = [channel for channel, _ in pipeline.notifier.sent]
    assert "slack" in channels
    assert "email" in channels  # a human decision is needed, so a human is told


async def test_proceed_does_not_email(kb, fake_brain, make_interview):
    iv = make_interview()
    add_turns(iv, [("interviewer", "Q"), ("candidate", "A")])
    mark(iv, InterviewStatus.completed)
    pipeline = build_pipeline(kb, fake_brain())
    await pipeline.run(iv)
    assert [c for c, _ in pipeline.notifier.sent] == ["slack"]


async def test_sheet_row_matches_the_header_order(kb, fake_brain, make_interview):
    iv = make_interview()
    add_turns(iv, [("interviewer", "Q"), ("candidate", "A")])
    mark(iv, InterviewStatus.completed)
    pipeline = build_pipeline(kb, fake_brain())
    await pipeline.run(iv)

    row = pipeline.sheets.rows[0]
    values = row_to_values(row)
    assert len(values) == len(HEADER)
    assert values[HEADER.index("Interview ID")] == iv
    assert values[HEADER.index("Recommendation")] == "PROCEED"


async def test_a_sink_failure_does_not_lose_the_scorecard(kb, fake_brain, make_interview):
    """A monday.com outage must not cost us the evaluation."""
    iv = make_interview()
    add_turns(iv, [("interviewer", "Q"), ("candidate", "A")])
    mark(iv, InterviewStatus.completed)

    class BrokenMonday(NullMondaySink):
        async def upsert_candidate(self, **kwargs):
            raise RuntimeError("monday.com is down")

    pipeline = ResultsPipeline(
        brain=fake_brain(),
        sheets=RecordingSheets(),
        monday=BrokenMonday(),
        notifier=Notifier(),
        knowledge=kb,
    )
    await pipeline.run(iv)

    with SessionLocal() as session:
        sc = session.get(Interview, iv).scorecard
        assert sc is not None and sc.overall_score == 80.0
        assert sc.monday_item_id is None
        assert sc.sheet_row is not None  # the sheet write still landed

"""Test fixtures.

The database URL is set before any app module is imported, because `app.db`
builds its engine at import time from settings.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="hr-agents-tests-"), "test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite+pysqlite:///{_TMP_DB}")
os.environ.setdefault("INTERNAL_API_KEY", "test-key")
os.environ.setdefault("RECALL_REALTIME_TOKEN", "test-realtime-token")
os.environ.setdefault("PUBLIC_BASE_URL", "https://interviewer.example.com")
os.environ.setdefault("ANSWER_DEBOUNCE_S", "0.05")
os.environ.setdefault("EARLY_EXIT_CHECK_CATEGORIES", "")

import pytest  # noqa: E402

from app.clients.llm import CategoryScore, Evaluation, TurnDecision  # noqa: E402
from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.interview.knowledge import load_knowledge  # noqa: E402
from app.interview.voice import CollectingVoice  # noqa: E402
from app.models import Base, Candidate, Interview, InterviewStatus  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    init_db()
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with SessionLocal() as session:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()


@pytest.fixture
def kb():
    return load_knowledge()


@pytest.fixture
def voice():
    return CollectingVoice()


class FakeBrain:
    """Deterministic brain with a scripted decision queue.

    `decisions` is consumed in order; when it runs dry the brain advances to the
    next planned question, then ends. `facts` is what mid-interview extraction
    returns.
    """

    def __init__(self, decisions=None, facts=None, evaluation=None):
        self.decisions = list(decisions or [])
        self.facts = facts or {}
        self._evaluation = evaluation
        self.turn_calls: list[dict] = []
        self.evaluate_calls: list[dict] = []

    def decide_turn(self, **kwargs):
        self.turn_calls.append(kwargs)
        if self.decisions:
            return self.decisions.pop(0)
        if kwargs.get("forced_end_reason") or kwargs.get("next_question") is None:
            return TurnDecision(action="END_INTERVIEW", speech="Thanks, goodbye.")
        nxt = kwargs["next_question"]
        return TurnDecision(
            action="NEXT_QUESTION", speech=str(nxt["text"]), question_id=str(nxt["id"])
        )

    def extract_facts(self, **kwargs):
        return dict(self.facts)

    def evaluate(self, **kwargs):
        self.evaluate_calls.append(kwargs)
        if self._evaluation is not None:
            return self._evaluation
        kb_ = kwargs["kb"]
        return Evaluation(
            category_scores=[
                CategoryScore(key=c.key, score=8.0, justification="fake") for c in kb_.categories
            ],
            summary="Fake evaluation.",
            strengths=["clear communicator"],
            concerns=[],
            facts=dict(self.facts),
        )


@pytest.fixture
def fake_brain():
    return FakeBrain


@pytest.fixture
def make_interview():
    """Insert a candidate + scheduled interview and return the interview id."""

    def _make(
        *,
        role: str = "default",
        name: str = "Ana Santos",
        email: str = "ana@example.com",
        status: InterviewStatus = InterviewStatus.scheduled,
        bot_id: str | None = "bot-123",
        minutes_out: int = 60,
    ) -> str:
        with SessionLocal() as session:
            candidate = Candidate(
                full_name=name, email=email, role_applied=role, phone="+639170000000"
            )
            session.add(candidate)
            session.flush()
            interview = Interview(
                candidate_id=candidate.id,
                scheduled_at=datetime.now(UTC) + timedelta(minutes=minutes_out),
                status=status,
                bot_id=bot_id,
            )
            session.add(interview)
            session.commit()
            return interview.id

    return _make

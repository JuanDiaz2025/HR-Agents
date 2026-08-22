"""The AI Interviewer Engine — the "Interview Flow (Inside the Meeting)" box.

    greet -> ask -> listen -> decide (follow up / move on) -> ... -> close

Transport-agnostic by design. It consumes utterances and emits speech through
injected adapters, so the same code path drives a real Google Meet call and the
offline simulator.

Durable state lives in the database (an interview survives a process restart
mid-call). Per-call timing state — the silence debounce, the "am I currently
talking" window — lives in memory, which is why the service must run with a
single worker unless you move `_runtimes` into Redis.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.llm import Brain, TurnDecision
from app.config import Settings, get_settings
from app.db import session_scope
from app.interview.knowledge import Knowledge, Question, cached_knowledge
from app.interview.scoring import early_exit_reason
from app.interview.voice import VoiceOutput
from app.models import Interview, InterviewStatus, Speaker, TranscriptTurn, utcnow

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    """In-memory, per-live-call state."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    buffer: list[str] = field(default_factory=list)
    speaker_name: str | None = None
    debounce_task: asyncio.Task | None = None
    # Wall-clock instant until which our own voice is still playing, and the
    # text we are playing. Used to recognise our own voice echoing back in the
    # transcript without discarding a candidate who talks over us.
    speaking_until: float = 0.0
    speaking_text: str = ""
    pending_end_reason: str | None = None
    closing: bool = False


class _SafeDict(dict):
    """Leaves unknown placeholders untouched instead of raising KeyError."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class InterviewEngine:
    def __init__(
        self,
        *,
        brain: Brain,
        voice: VoiceOutput,
        knowledge: Knowledge | None = None,
        settings: Settings | None = None,
        on_finished: Any = None,
    ):
        self.brain = brain
        self.voice = voice
        self.kb = knowledge or cached_knowledge()
        self.settings = settings or get_settings()
        # Called as `await on_finished(interview_id)` once the call has ended;
        # this is where the results pipeline hooks in.
        self.on_finished = on_finished
        self._runtimes: dict[str, Runtime] = {}

    # ------------------------------------------------------------------ helpers
    def runtime(self, interview_id: str) -> Runtime:
        return self._runtimes.setdefault(interview_id, Runtime())

    def release(self, interview_id: str) -> None:
        rt = self._runtimes.pop(interview_id, None)
        if rt and rt.debounce_task and not rt.debounce_task.done():
            rt.debounce_task.cancel()

    def _plan(self, interview: Interview) -> list[Question]:
        role = interview.candidate.role_applied or "default"
        questions = {q.id: q for q in self.kb.questions_for(role)}
        planned = [questions[qid] for qid in interview.question_plan if qid in questions]
        return planned or self.kb.questions_for(role)

    @staticmethod
    def _q_dict(question: Question | None) -> dict[str, Any] | None:
        if question is None:
            return None
        return {"id": question.id, "text": question.text, "probe_for": list(question.probe_for)}

    def _transcript(self, session: Session, interview_id: str) -> list[dict[str, str]]:
        rows = session.scalars(
            select(TranscriptTurn)
            .where(TranscriptTurn.interview_id == interview_id)
            .order_by(TranscriptTurn.sequence)
        ).all()
        return [
            {"speaker": r.speaker.value, "text": r.text}
            for r in rows
            if r.speaker in (Speaker.interviewer, Speaker.candidate)
        ]

    def _append_turn(
        self,
        session: Session,
        interview: Interview,
        *,
        speaker: Speaker,
        text: str,
        question_id: str | None = None,
        speaker_name: str | None = None,
        started_at_s: float | None = None,
    ) -> None:
        next_seq = (
            session.scalar(
                select(TranscriptTurn.sequence)
                .where(TranscriptTurn.interview_id == interview.id)
                .order_by(TranscriptTurn.sequence.desc())
                .limit(1)
            )
            or 0
        ) + 1
        session.add(
            TranscriptTurn(
                interview_id=interview.id,
                sequence=next_seq,
                speaker=speaker,
                speaker_name=speaker_name,
                text=text.strip(),
                question_id=question_id,
                started_at_s=started_at_s,
            )
        )

    def _fill(self, interview: Interview, text: str) -> str:
        """Substitute script placeholders. Applied on every utterance because the
        opening, the closing and any model-echoed template all pass through here
        — a leaked "{first_name}" is spoken aloud to a real candidate."""
        if "{" not in text:
            return text
        full_name = interview.candidate.full_name or ""
        return text.format_map(
            _SafeDict(
                first_name=full_name.split()[0] if full_name.split() else "there",
                full_name=full_name,
                company=self.kb.company.get("name", "our company"),
                duration=self.settings.interview_duration_minutes,
                role=interview.candidate.role_applied or "the role",
            )
        )

    async def _say(
        self, interview_id: str, text: str, *, question_id: str | None = None
    ) -> None:
        """Speak, record it in the transcript, and note how long we'll be talking.

        Takes an id rather than an ORM object: callers hold rows loaded in
        sessions that have since closed, and reading a relationship off one of
        those raises DetachedInstanceError mid-call.
        """
        with session_scope() as session:
            interview = session.get(Interview, interview_id)
            if interview is None:
                return
            spoken = " ".join(self._fill(interview, text).split())
            if not spoken:
                return
            bot_id = interview.bot_id
            self._append_turn(
                session, interview, speaker=Speaker.interviewer, text=spoken,
                question_id=question_id,
            )

        rt = self.runtime(interview_id)
        words = max(1, len(spoken.split()))
        rt.speaking_until = asyncio.get_running_loop().time() + words / max(
            0.5, self.settings.speech_words_per_second
        )
        rt.speaking_text = spoken

        await self.voice.speak(bot_id, spoken)
        log.info("[%s] interviewer: %s", interview_id[:8], spoken)

    def _elapsed(self, interview: Interview) -> int:
        started = interview.started_at or interview.created_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        return int((datetime.now(UTC) - started).total_seconds())

    # -------------------------------------------------------------------- start
    async def start(self, interview_id: str) -> None:
        """Bot is in the call and recording — greet the candidate."""
        with session_scope() as session:
            interview = session.get(Interview, interview_id)
            if interview is None:
                log.warning("start: unknown interview %s", interview_id)
                return
            if interview.status in (InterviewStatus.in_progress, InterviewStatus.completed):
                log.info("start: interview %s already %s", interview_id[:8], interview.status.value)
                return

            role = interview.candidate.role_applied or "default"
            plan = self.kb.questions_for(role)
            interview.question_plan = [q.id for q in plan]
            interview.plan_cursor = 0
            interview.followups_used = 0
            interview.questions_asked = 0
            interview.status = InterviewStatus.in_progress
            interview.started_at = utcnow()
            opening, _ = self.kb.script_for(role)

        await self._say(interview_id, opening)

    def _is_own_speech(self, rt: Runtime, text: str, speaker_name: str | None) -> bool:
        """Recognise the bot's own voice coming back through transcription.

        Speaker attribution is the reliable signal. The text comparison is the
        fallback for platforms that attribute bot audio to no participant — it
        is deliberately narrow so a candidate talking over us is still heard,
        which is the whole point of capturing an interruption.
        """
        if speaker_name and speaker_name.strip().lower() == self.settings.bot_name.strip().lower():
            return True
        if asyncio.get_running_loop().time() >= rt.speaking_until or not rt.speaking_text:
            return False

        spoken = {w.strip(".,!?;:'\"").lower() for w in rt.speaking_text.split()}
        heard = [w.strip(".,!?;:'\"").lower() for w in text.split()]
        # Short utterances are not safe to match this way: "Ready." or "Yes"
        # legitimately echoes a word from the question we just asked, and
        # dropping it would strand the interview waiting for an answer already
        # given. A stray short echo, by contrast, is harmless — it joins the
        # buffer alongside the real answer.
        if len(heard) < 4 or not spoken:
            return False
        overlap = sum(1 for w in heard if w in spoken) / len(heard)
        return overlap >= 0.7

    # ---------------------------------------------------------------- utterance
    async def on_utterance(
        self,
        interview_id: str,
        *,
        text: str,
        speaker_name: str | None = None,
        started_at_s: float | None = None,
    ) -> None:
        """A finalised candidate utterance arrived. Buffer it and (re)arm the
        silence timer — people pause mid-thought, and reacting to the first
        fragment makes the AI interrupt."""
        text = " ".join(text.split())
        if not text:
            return
        rt = self.runtime(interview_id)
        if rt.closing:
            return

        if self._is_own_speech(rt, text, speaker_name):
            log.debug("[%s] ignoring own speech in transcript: %s", interview_id[:8], text)
            return

        rt.buffer.append(text)
        rt.speaker_name = speaker_name or rt.speaker_name
        log.info("[%s] candidate: %s", interview_id[:8], text)

        if rt.debounce_task and not rt.debounce_task.done():
            rt.debounce_task.cancel()
        rt.debounce_task = asyncio.create_task(self._debounce(interview_id))

    async def _debounce(self, interview_id: str) -> None:
        try:
            await asyncio.sleep(self.settings.answer_debounce_s)
        except asyncio.CancelledError:
            return
        try:
            await self.advance(interview_id)
        except Exception:  # pragma: no cover - never let a background task die silently
            log.exception("[%s] failed to advance interview", interview_id[:8])

    # ------------------------------------------------------------------ advance
    async def advance(self, interview_id: str) -> TurnDecision | None:
        """The candidate has finished answering. Decide and speak the next turn."""
        rt = self.runtime(interview_id)
        async with rt.lock:
            answer = " ".join(rt.buffer).strip()
            rt.buffer.clear()
            speaker_name = rt.speaker_name

            with session_scope() as session:
                interview = session.get(Interview, interview_id)
                if interview is None or interview.status is not InterviewStatus.in_progress:
                    return None

                plan = self._plan(interview)
                cursor = interview.plan_cursor
                current = plan[cursor - 1] if 0 < cursor <= len(plan) else None
                nxt = plan[cursor] if cursor < len(plan) else None

                if answer:
                    self._append_turn(
                        session,
                        interview,
                        speaker=Speaker.candidate,
                        text=answer,
                        question_id=current.id if current else None,
                        speaker_name=speaker_name,
                    )
                    session.flush()

                transcript = self._transcript(session, interview_id)
                max_followups = (
                    current.max_followups
                    if current and current.max_followups is not None
                    else self.settings.max_followups_per_question
                )
                elapsed = self._elapsed(interview)
                seconds_remaining = max(0, self.settings.max_interview_seconds - elapsed)

                role = interview.candidate.role_applied or "default"
                followups_used = interview.followups_used
                questions_asked = interview.questions_asked

            # A completed question in a watched category is where hard blockers
            # surface. Check before deciding, so a confirmed blocker ends this
            # turn rather than buying the candidate another question.
            if current and current.category in self.settings.early_exit_check_categories:
                await self._maybe_early_exit(interview_id, transcript)

            forced = rt.pending_end_reason
            if forced is None:
                if questions_asked >= self.settings.max_questions:
                    forced = "question limit reached"
                elif seconds_remaining <= 0:
                    forced = "time limit reached"

            decision = await asyncio.to_thread(
                self.brain.decide_turn,
                kb=self.kb,
                role=role,
                transcript=transcript,
                current_question=self._q_dict(current),
                next_question=self._q_dict(nxt),
                followups_used=followups_used,
                max_followups=max_followups,
                questions_remaining=max(0, len(plan) - cursor),
                seconds_remaining=seconds_remaining,
                forced_end_reason=forced,
            )

            # Guardrails the model does not get to override.
            if forced:
                decision.action = "END_INTERVIEW"
            elif decision.action == "ASK_FOLLOWUP" and followups_used >= max_followups:
                log.info(
                    "[%s] follow-up cap hit on %s — advancing instead",
                    interview_id[:8],
                    current.id if current else "?",
                )
                if nxt is None:
                    decision.action = "END_INTERVIEW"
                    _, closing = self.kb.script_for(role)
                    decision.speech = closing
                else:
                    decision.action = "NEXT_QUESTION"
                    decision.speech = f"Understood. {nxt.text}"
                    decision.question_id = nxt.id

            if decision.action == "END_INTERVIEW":
                rt.closing = True
                await self._say(interview_id, decision.speech)
                await self.finish(interview_id, reason=forced or "plan complete")
                return decision

            advanced_to: Question | None = None
            with session_scope() as session:
                interview = session.get(Interview, interview_id)
                if interview is None:
                    return decision
                if decision.action == "ASK_FOLLOWUP":
                    interview.followups_used = followups_used + 1
                elif nxt is not None and (
                    decision.question_id == nxt.id or decision.action == "NEXT_QUESTION"
                ):
                    interview.plan_cursor = cursor + 1
                    interview.questions_asked += 1
                    interview.followups_used = 0
                    advanced_to = nxt

            await self._say(
                interview_id,
                decision.speech,
                question_id=advanced_to.id if advanced_to else (current.id if current else None),
            )
            return decision

    async def _maybe_early_exit(self, interview_id: str, transcript: list[dict[str, str]]) -> None:
        rt = self.runtime(interview_id)
        if rt.pending_end_reason:
            return
        try:
            facts = await asyncio.to_thread(
                self.brain.extract_facts, kb=self.kb, transcript=transcript
            )
        except Exception:
            log.exception("[%s] mid-interview fact extraction failed", interview_id[:8])
            return
        reason = early_exit_reason(self.kb, facts or {})
        if reason:
            log.info("[%s] early-exit blocker confirmed: %s", interview_id[:8], reason)
            rt.pending_end_reason = reason

    # ------------------------------------------------------------------- finish
    async def finish(self, interview_id: str, *, reason: str = "call ended") -> None:
        """The call is over. Freeze the interview and hand off to the pipeline."""
        rt = self._runtimes.get(interview_id)
        if rt and rt.debounce_task and not rt.debounce_task.done():
            rt.debounce_task.cancel()

        # A call that ends right after the last answer leaves that answer sitting
        # in the debounce buffer. Persist it before freezing, or the candidate's
        # final words never reach the transcript or the scorecard.
        if rt and rt.buffer:
            pending = " ".join(rt.buffer).strip()
            rt.buffer.clear()
            if pending:
                with session_scope() as session:
                    interview = session.get(Interview, interview_id)
                    if interview is not None:
                        plan = self._plan(interview)
                        cursor = interview.plan_cursor
                        current = plan[cursor - 1] if 0 < cursor <= len(plan) else None
                        self._append_turn(
                            session,
                            interview,
                            speaker=Speaker.candidate,
                            text=pending,
                            question_id=current.id if current else None,
                            speaker_name=rt.speaker_name,
                        )
                log.info("[%s] flushed buffered answer at end of call", interview_id[:8])

        with session_scope() as session:
            interview = session.get(Interview, interview_id)
            if interview is None:
                return
            if interview.status in (InterviewStatus.completed, InterviewStatus.no_show):
                return

            answered = any(t.speaker is Speaker.candidate for t in interview.turns)
            if interview.status is InterviewStatus.in_progress and answered:
                interview.status = InterviewStatus.completed
            elif not answered:
                interview.status = InterviewStatus.no_show
            interview.ended_at = utcnow()
            interview.end_reason = reason
            final_status = interview.status

        log.info("[%s] interview finished status=%s reason=%s", interview_id[:8], final_status.value, reason)
        self.release(interview_id)
        if self.on_finished is not None:
            await self.on_finished(interview_id)

    async def fail(self, interview_id: str, *, error: str) -> None:
        with session_scope() as session:
            interview = session.get(Interview, interview_id)
            if interview is None:
                return
            interview.status = InterviewStatus.failed
            interview.error = error[:2000]
            interview.ended_at = utcnow()
            interview.end_reason = "bot fatal"
        log.error("[%s] interview failed: %s", interview_id[:8], error)
        self.release(interview_id)

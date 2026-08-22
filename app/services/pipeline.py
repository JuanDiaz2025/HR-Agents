"""Post-interview pipeline — the "WEBHOOK & AUTOMATION FLOW" band.

    transcript -> score (LLM) -> rules (code) -> Sheets -> monday.com -> notify

Every step is idempotent: re-running it for the same interview updates the same
sheet row and the same monday item rather than creating duplicates.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.clients.llm import Brain
from app.clients.monday import MondaySink
from app.clients.notify import Notifier
from app.clients.sheets import SheetsSink
from app.config import Settings, get_settings
from app.db import session_scope
from app.interview.knowledge import Knowledge, cached_knowledge
from app.interview.scoring import score_interview
from app.models import (
    Interview,
    InterviewStatus,
    Recommendation,
    Scorecard,
    Speaker,
    utcnow,
)

log = logging.getLogger(__name__)


class ResultsPipeline:
    def __init__(
        self,
        *,
        brain: Brain,
        sheets: SheetsSink,
        monday: MondaySink,
        notifier: Notifier,
        knowledge: Knowledge | None = None,
        settings: Settings | None = None,
    ):
        self.brain = brain
        self.sheets = sheets
        self.monday = monday
        self.notifier = notifier
        self.kb = knowledge or cached_knowledge()
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------- public
    async def run(self, interview_id: str) -> Scorecard | None:
        """Score and publish. Safe to call more than once."""
        scorecard = await self.score(interview_id)
        await self.publish(interview_id)
        return scorecard

    # -------------------------------------------------------------------- score
    async def score(self, interview_id: str) -> Scorecard | None:
        import asyncio

        with session_scope() as session:
            interview = session.get(Interview, interview_id)
            if interview is None:
                log.warning("score: unknown interview %s", interview_id)
                return None

            transcript = [
                {"speaker": t.speaker.value, "text": t.text}
                for t in interview.turns
                if t.speaker in (Speaker.interviewer, Speaker.candidate)
            ]
            candidate_answers = [t for t in transcript if t["speaker"] == "candidate"]
            candidate_meta = {
                "name": interview.candidate.full_name,
                "email": interview.candidate.email,
                "role_applied": interview.candidate.role_applied,
                "source": interview.candidate.source,
            }
            role = interview.candidate.role_applied or "default"
            duration = interview.duration_seconds
            status = interview.status

        # A no-show has nothing to score, but still needs a record downstream.
        if status is InterviewStatus.no_show or not candidate_answers:
            return self._save_scorecard(
                interview_id,
                overall=0.0,
                recommendation=Recommendation.do_not_proceed
                if status is InterviewStatus.no_show
                else Recommendation.review,
                summary=(
                    "Candidate did not join or did not speak. No interview took place."
                    if status is InterviewStatus.no_show
                    else "No candidate speech was captured — review the recording manually."
                ),
                category_scores=[],
                strengths=[],
                concerns=["No candidate answers were captured."],
                facts={},
                disqualified=status is InterviewStatus.no_show,
                reasons=["Did not attend the scheduled interview."]
                if status is InterviewStatus.no_show
                else [],
            )

        evaluation = await asyncio.to_thread(
            self.brain.evaluate,
            kb=self.kb,
            role=role,
            candidate=candidate_meta,
            transcript=transcript,
            duration_seconds=duration,
        )

        raw_scores = {c.key: c.score for c in evaluation.category_scores}
        result = score_interview(self.kb, raw_scores, dict(evaluation.facts))

        justifications = {c.key: c.justification for c in evaluation.category_scores}
        rows = [
            {**row, "justification": justifications.get(row["key"], "")}
            for row in result.category_scores
        ]

        return self._save_scorecard(
            interview_id,
            overall=result.overall_score,
            recommendation=result.recommendation,
            summary=evaluation.summary,
            category_scores=rows,
            strengths=list(evaluation.strengths),
            concerns=list(evaluation.concerns),
            facts=dict(evaluation.facts),
            disqualified=result.disqualified,
            reasons=result.disqualification_reasons,
        )

    def _save_scorecard(
        self,
        interview_id: str,
        *,
        overall: float,
        recommendation: Recommendation,
        summary: str,
        category_scores: list[dict[str, Any]],
        strengths: list[str],
        concerns: list[str],
        facts: dict[str, Any],
        disqualified: bool,
        reasons: list[str],
    ) -> Scorecard | None:
        with session_scope() as session:
            interview = session.get(Interview, interview_id)
            if interview is None:
                return None
            scorecard = interview.scorecard or Scorecard(interview_id=interview_id)
            scorecard.overall_score = overall
            scorecard.recommendation = recommendation
            scorecard.summary = summary
            scorecard.category_scores = category_scores
            scorecard.strengths = strengths
            scorecard.concerns = concerns
            scorecard.facts = facts
            scorecard.disqualified = disqualified
            scorecard.disqualification_reasons = reasons
            session.add(scorecard)
            session.flush()
            log.info(
                "[%s] scored %.1f/100 -> %s%s",
                interview_id[:8],
                overall,
                recommendation.value,
                " (disqualified)" if disqualified else "",
            )
            return scorecard

    # ------------------------------------------------------------------ publish
    async def publish(self, interview_id: str) -> None:
        with session_scope() as session:
            interview = session.get(Interview, interview_id)
            if interview is None or interview.scorecard is None:
                log.warning("publish: nothing to publish for %s", interview_id)
                return
            sc = interview.scorecard
            candidate = interview.candidate
            transcript_url = f"{self.settings.public_base_url}/api/interviews/{interview.id}/transcript"
            row = {
                "Interview ID": interview.id,
                "Candidate": candidate.full_name,
                "Email": candidate.email,
                "Phone": candidate.phone or "",
                "Role": candidate.role_applied,
                "Scheduled At": interview.scheduled_at.isoformat() if interview.scheduled_at else "",
                "Status": interview.status.value,
                "Duration (s)": interview.duration_seconds or 0,
                "Overall Score": sc.overall_score,
                "Recommendation": sc.recommendation.value,
                "Disqualified": "YES" if sc.disqualified else "NO",
                "Disqualification Reasons": "; ".join(sc.disqualification_reasons),
                "Summary": sc.summary,
                "Strengths": "; ".join(sc.strengths),
                "Concerns": "; ".join(sc.concerns),
                "Category Scores": json.dumps(
                    {c["key"]: c["score"] for c in sc.category_scores}
                ),
                "Meeting URL": interview.meeting_url or "",
                "Bot ID": interview.bot_id or "",
            }
            monday_fields = {
                "email": candidate.email,
                "phone": candidate.phone,
                "role": candidate.role_applied,
                "overall_score": sc.overall_score,
                "interview_date": interview.scheduled_at.date().isoformat()
                if interview.scheduled_at
                else None,
                "summary": sc.summary[:1000],
                "transcript_url": transcript_url,
                **{
                    f"score_{c['key']}": c["score"] for c in sc.category_scores
                },
            }
            note = self._monday_note(sc, interview)
            name = candidate.full_name
            recommendation = sc.recommendation
            scorecard_id = sc.id

        # --- Google Sheets ---
        try:
            sheet_row = self.sheets.upsert_row(row)
        except Exception:
            log.exception("[%s] sheets write failed", interview_id[:8])
            sheet_row = None

        # --- monday.com ---
        try:
            item_id = await self.monday.upsert_candidate(
                name=name, recommendation=recommendation, note=note, **monday_fields
            )
        except Exception:
            log.exception("[%s] monday.com update failed", interview_id[:8])
            item_id = None

        with session_scope() as session:
            scorecard = session.get(Scorecard, scorecard_id)
            if scorecard is not None:
                if sheet_row:
                    scorecard.sheet_row = sheet_row
                if item_id:
                    scorecard.monday_item_id = str(item_id)
                scorecard.published_at = utcnow()

        await self._notify(name, recommendation, row, note)

    @staticmethod
    def _monday_note(sc: Scorecard, interview: Interview) -> str:
        lines = [f"*AI screening result: {sc.recommendation.value}* ({sc.overall_score}/100)", ""]
        for cat in sc.category_scores:
            lines.append(f"- {cat['label']}: {cat['score']}/{cat['max']:g} — {cat.get('justification', '')}")
        lines += ["", sc.summary]
        if sc.strengths:
            lines += ["", "Strengths:"] + [f"- {s}" for s in sc.strengths]
        if sc.concerns:
            lines += ["", "Concerns:"] + [f"- {c}" for c in sc.concerns]
        if sc.disqualification_reasons:
            lines += ["", "Disqualified:"] + [f"- {r}" for r in sc.disqualification_reasons]
        lines += ["", f"Interview ID: {interview.id}"]
        return "\n".join(lines)

    async def _notify(
        self, name: str, recommendation: Recommendation, row: dict[str, Any], note: str
    ) -> None:
        emoji = {
            Recommendation.proceed: ":white_check_mark:",
            Recommendation.review: ":eyes:",
            Recommendation.do_not_proceed: ":x:",
        }[recommendation]
        headline = (
            f"{emoji} AI interview complete — *{name}* ({row['Role']}): "
            f"{recommendation.value} at {row['Overall Score']}/100"
        )
        await self.notifier.slack(headline)

        # Only interrupt a human when a human decision is actually needed.
        if recommendation is Recommendation.review:
            self.notifier.email(
                subject=f"[Review needed] AI interview — {name} ({row['Role']})",
                body=f"{headline}\n\n{note}\n\nTranscript: {row['Interview ID']}",
            )

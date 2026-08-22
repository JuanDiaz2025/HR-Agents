"""Stages 4-7: pending row in, decision written back.

One row's failure is that row's failure. Anything that goes wrong is recorded in
the row's Error column and the batch continues — a bad video link on submission
three must not strand the twenty behind it.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import media
from .evaluator import Evaluator
from .models import ScoredEvaluation
from .rubric import Rubric
from .scoring import score_evaluation
from .store import STATUS, Submission, SubmissionStore, error_columns, result_columns
from .transcribe import Transcriber

logger = logging.getLogger(__name__)


@dataclass
class BatchReport:
    evaluated: list[ScoredEvaluation]
    failed: list[tuple[str, str]]

    @property
    def counts(self) -> dict[str, int]:
        tally = {"PASS": 0, "NOT PASS": 0, "NEEDS REVIEW": 0, "ERROR": len(self.failed)}
        for evaluation in self.evaluated:
            tally[evaluation.decision] += 1
        return tally


class Pipeline:
    def __init__(
        self,
        store: SubmissionStore,
        evaluator: Evaluator,
        transcriber: Transcriber,
        rubric: Rubric,
        workdir: Path = Path("workdir"),
        frame_count: int = 6,
        max_video_seconds: int = 900,
        drive_service=None,
    ):
        self.store = store
        self.evaluator = evaluator
        self.transcriber = transcriber
        self.rubric = rubric
        self.workdir = Path(workdir)
        self.frame_count = frame_count
        self.max_video_seconds = max_video_seconds
        self.drive_service = drive_service

    def run(self, limit: int | None = None) -> BatchReport:
        pending = self.store.pending()
        if limit is not None:
            pending = pending[:limit]
        logger.info("%d submission(s) to evaluate", len(pending))

        report = BatchReport(evaluated=[], failed=[])
        for submission in pending:
            try:
                evaluation = self.process(submission)
            except Exception as exc:  # one bad row must not stop the batch
                logger.exception("submission %s failed", submission.submission_id)
                self.store.update(submission, error_columns(f"{type(exc).__name__}: {exc}"))
                report.failed.append((submission.submission_id, str(exc)))
            else:
                self.store.update(submission, result_columns(evaluation))
                report.evaluated.append(evaluation)
        return report

    def process(self, submission: Submission) -> ScoredEvaluation:
        self.store.update(submission, {STATUS: "PROCESSING"})
        scratch = self.workdir / submission.submission_id
        try:
            return self._process(submission, scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _process(self, submission: Submission, scratch: Path) -> ScoredEvaluation:
        video = media.fetch_video(submission.video_link, scratch, self.drive_service)
        duration = media.probe_duration(video)
        if duration > self.max_video_seconds:
            raise media.MediaError(
                f"Video is {duration:.0f}s, over the {self.max_video_seconds}s limit."
            )

        frames = media.sample_frames(video, self.frame_count, duration=duration)
        audio = media.extract_audio(video, scratch)
        transcript = self.transcriber.transcribe(audio)

        result = self.evaluator.evaluate(
            submission_id=submission.submission_id,
            rubric=self.rubric,
            form_response=submission.form_response(),
            transcript=transcript,
            frames=frames,
            duration=duration,
        )
        return score_evaluation(submission.submission_id, result, self.rubric)

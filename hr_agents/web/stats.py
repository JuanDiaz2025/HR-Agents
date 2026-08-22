"""Dashboard aggregates.

Pure functions over a list of submissions — no store, no request, no template.
The numbers a hiring decision gets judged by should be testable on their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..store import Submission

DECISIONS = ("PASS", "NOT PASS", "NEEDS REVIEW")
BUCKET_SIZE = 10


@dataclass(frozen=True)
class Bucket:
    low: int
    high: int
    count: int

    @property
    def label(self) -> str:
        return f"{self.low}-{self.high}"


@dataclass
class Dashboard:
    total: int = 0
    evaluated: int = 0
    errored: int = 0
    awaiting_evaluation: int = 0
    awaiting_review: int = 0
    reviewed: int = 0
    overridden: int = 0
    decisions: dict[str, int] = field(default_factory=dict)
    buckets: list[Bucket] = field(default_factory=list)
    flags: dict[str, int] = field(default_factory=dict)

    @property
    def decided(self) -> int:
        """Submissions with a final PASS or NOT PASS — review pending doesn't count."""
        return self.decisions.get("PASS", 0) + self.decisions.get("NOT PASS", 0)

    @property
    def pass_rate(self) -> float | None:
        if not self.decided:
            return None
        return 100.0 * self.decisions.get("PASS", 0) / self.decided

    @property
    def override_rate(self) -> float | None:
        """The number to watch. High means the rubric disagrees with your reviewers."""
        if not self.reviewed:
            return None
        return 100.0 * self.overridden / self.reviewed

    @property
    def max_bucket(self) -> int:
        return max((b.count for b in self.buckets), default=0)


def summarize(submissions: list[Submission]) -> Dashboard:
    dashboard = Dashboard(total=len(submissions))
    dashboard.decisions = {d: 0 for d in DECISIONS}
    counts = [0] * (100 // BUCKET_SIZE)
    flags: dict[str, int] = {}

    for submission in submissions:
        if submission.status == "ERROR":
            dashboard.errored += 1
        elif submission.status == "DONE":
            dashboard.evaluated += 1
        elif not submission.reviewer_decision:
            dashboard.awaiting_evaluation += 1

        if submission.reviewer_decision:
            dashboard.reviewed += 1
            if submission.was_overridden:
                dashboard.overridden += 1

        final = submission.final_decision
        if final in dashboard.decisions:
            dashboard.decisions[final] += 1
        if final == "NEEDS REVIEW" and not submission.reviewer_decision:
            dashboard.awaiting_review += 1

        score = submission.score
        if score is not None:
            counts[min(score, 99) // BUCKET_SIZE] += 1

        for flag in submission.flags:
            flags[flag] = flags.get(flag, 0) + 1

    dashboard.buckets = [
        Bucket(low=i * BUCKET_SIZE, high=i * BUCKET_SIZE + BUCKET_SIZE - 1, count=count)
        for i, count in enumerate(counts)
    ]
    dashboard.flags = dict(sorted(flags.items(), key=lambda kv: -kv[1]))
    return dashboard

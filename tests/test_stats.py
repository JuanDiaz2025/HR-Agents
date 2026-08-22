from hr_agents.store import Submission
from hr_agents.web.stats import summarize


def sub(row, **values):
    return Submission(row_number=row, values={"Submission ID": f"SUB-{row}", **values})


def test_empty_dashboard_reports_nothing_rather_than_zero_percent():
    stats = summarize([])
    assert stats.total == 0
    assert stats.pass_rate is None       # not 0% — there is no rate yet
    assert stats.override_rate is None


def test_pass_rate_ignores_rows_still_awaiting_review():
    stats = summarize([
        sub(2, **{"Status": "DONE", "AI Decision": "PASS", "AI Score": "90"}),
        sub(3, **{"Status": "DONE", "AI Decision": "NOT PASS", "AI Score": "40"}),
        sub(4, **{"Status": "DONE", "AI Decision": "NEEDS REVIEW", "AI Score": "78"}),
    ])
    assert stats.decided == 2
    assert stats.pass_rate == 50.0
    assert stats.awaiting_review == 1


def test_the_reviewers_decision_is_what_gets_counted():
    stats = summarize([
        sub(2, **{"Status": "DONE", "AI Decision": "PASS", "Reviewer Decision": "NOT PASS"}),
    ])
    assert stats.decisions["NOT PASS"] == 1
    assert stats.decisions["PASS"] == 0
    assert stats.overridden == 1
    assert stats.override_rate == 100.0


def test_agreement_is_not_an_override():
    stats = summarize([
        sub(2, **{"Status": "DONE", "AI Decision": "PASS", "Reviewer Decision": "PASS"}),
    ])
    assert stats.reviewed == 1
    assert stats.overridden == 0
    assert stats.override_rate == 0.0


def test_scores_land_in_ten_point_buckets():
    stats = summarize([
        sub(2, **{"AI Score": "0"}),
        sub(3, **{"AI Score": "45"}),
        sub(4, **{"AI Score": "49"}),
        sub(5, **{"AI Score": "100"}),
    ])
    counts = {b.label: b.count for b in stats.buckets}
    assert counts["0-9"] == 1
    assert counts["40-49"] == 2
    assert counts["90-99"] == 1     # 100 folds into the top bucket
    assert stats.max_bucket == 2


def test_errors_are_counted_separately_from_evaluated():
    stats = summarize([
        sub(2, **{"Status": "ERROR", "Error": "no video"}),
        sub(3, **{"Status": "DONE", "AI Decision": "PASS"}),
        sub(4, **{"Video Link": "x.mp4"}),
    ])
    assert stats.errored == 1
    assert stats.evaluated == 1
    assert stats.awaiting_evaluation == 1


def test_flags_are_tallied_across_submissions():
    stats = summarize([
        sub(2, **{"Flags": "unintelligible_audio; video_truncated"}),
        sub(3, **{"Flags": "unintelligible_audio"}),
    ])
    assert stats.flags == {"unintelligible_audio": 2, "video_truncated": 1}

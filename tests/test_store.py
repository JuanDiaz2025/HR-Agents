import csv

import pytest

from hr_agents.store import CsvStore, Submission, error_columns, result_columns
from hr_agents.scoring import score_evaluation
from tests.conftest import make_result

HEADERS = ["Timestamp", "Submission ID", "Name", "Why this role?", "Video Link"]


def write_csv(path, rows, headers=HEADERS):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    return CsvStore(path)


def row(**overrides):
    base = {
        "Timestamp": "2026-05-20 10:15",
        "Submission ID": "SUB-1",
        "Name": "Jane Smith",
        "Why this role?": "I like operations work.",
        "Video Link": "https://example.com/a.mp4",
    }
    base.update(overrides)
    return base


def test_pending_picks_up_a_fresh_row(tmp_path):
    store = write_csv(tmp_path / "s.csv", [row()])
    pending = store.pending()
    assert [s.submission_id for s in pending] == ["SUB-1"]


def test_row_without_a_video_is_not_pending(tmp_path):
    store = write_csv(tmp_path / "s.csv", [row(**{"Video Link": ""})])
    assert store.pending() == []


def test_reviewed_row_is_never_reprocessed(tmp_path):
    headers = HEADERS + ["Status", "Reviewer Decision"]
    store = write_csv(
        tmp_path / "s.csv",
        [row(**{"Status": "PENDING", "Reviewer Decision": "PASS"})],
        headers=headers,
    )
    assert store.pending() == []


def test_errored_row_is_retried(tmp_path):
    store = write_csv(tmp_path / "s.csv", [row(**{"Status": "ERROR"})], headers=HEADERS + ["Status"])
    assert len(store.pending()) == 1


def test_done_row_is_not_reprocessed(tmp_path):
    store = write_csv(tmp_path / "s.csv", [row(**{"Status": "DONE"})], headers=HEADERS + ["Status"])
    assert store.pending() == []


def test_form_response_excludes_pipeline_and_reviewer_columns():
    submission = Submission(
        row_number=2,
        values={
            "Name": "Jane",
            "Why this role?": "Ops",
            "Video Link": "x",
            "Submission ID": "SUB-1",
            "AI Score": "87",
            "Reviewer Decision": "PASS",
            "Blank Answer": "",
        },
    )
    assert submission.form_response() == {"Name": "Jane", "Why this role?": "Ops"}


def test_update_refuses_to_write_outside_pipeline_columns(tmp_path):
    store = write_csv(tmp_path / "s.csv", [row()])
    submission = store.pending()[0]
    with pytest.raises(ValueError, match="non-pipeline"):
        store.update(submission, {"Name": "Somebody Else"})


def test_update_persists_and_leaves_form_columns_intact(tmp_path, rubric):
    path = tmp_path / "s.csv"
    store = write_csv(path, [row()])
    submission = store.pending()[0]
    evaluation = score_evaluation("SUB-1", make_result(rubric, score=90), rubric)

    store.update(submission, result_columns(evaluation))

    with path.open(newline="", encoding="utf-8") as handle:
        saved = next(csv.DictReader(handle))
    assert saved["Name"] == "Jane Smith"
    assert saved["AI Score"] == "90"
    assert saved["AI Decision"] == "PASS"
    assert saved["Status"] == "DONE"
    assert store.pending() == []


def test_error_columns_truncate_a_runaway_message():
    columns = error_columns("x" * 5000)
    assert len(columns["Error"]) == 1000
    assert columns["Status"] == "ERROR"

"""The app, exercised through real HTTP requests against a CSV-backed store."""

import csv

import pytest
from fastapi.testclient import TestClient

from hr_agents.config import Settings
from hr_agents.store import CsvStore, result_columns, review_columns
from hr_agents.scoring import score_evaluation
from hr_agents.web.app import create_app
from tests.conftest import make_result

HEADERS = ["Timestamp", "Submission ID", "Name", "Email", "Video Link"]


@pytest.fixture
def app_env(tmp_path, rubric):
    path = tmp_path / "submissions.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        for i in (1, 2):
            writer.writerow(
                {
                    "Timestamp": "2026-05-20",
                    "Submission ID": f"SUB-{i}",
                    "Name": f"Applicant {i}",
                    "Email": f"a{i}@example.com",
                    "Video Link": f"https://example.com/{i}.mp4",
                }
            )
    store = CsvStore(path)
    uploads = tmp_path / "uploads"
    app = create_app(
        settings=Settings(csv_path=path),
        store=store,
        rubric=rubric,
        uploads_dir=uploads,
    )
    return TestClient(app), store, uploads


def evaluate(store, rubric, submission_id, **kwargs):
    submission = store.get(submission_id)
    evaluation = score_evaluation(submission_id, make_result(rubric, **kwargs), rubric)
    store.update(submission, result_columns(evaluation))
    return evaluation


def test_health_reports_the_loaded_rubric(app_env, rubric):
    client, _, _ = app_env
    body = client.get("/health").json()
    assert body == {"status": "ok", "rubric": rubric.name, "rubric_version": rubric.version}


def test_dashboard_renders_with_no_data(app_env):
    client, _, _ = app_env
    response = client.get("/")
    assert response.status_code == 200
    assert "No decisions yet" in response.text


def test_dashboard_shows_the_pass_rate_once_there_are_decisions(app_env, rubric):
    client, store, _ = app_env
    evaluate(store, rubric, "SUB-1", score=90)
    evaluate(store, rubric, "SUB-2", score=40)

    text = client.get("/").text
    assert "50%" in text            # one pass, one not pass
    assert "Applicant 1" in text


def test_submissions_filter_by_decision(app_env, rubric):
    client, store, _ = app_env
    evaluate(store, rubric, "SUB-1", score=90)
    evaluate(store, rubric, "SUB-2", score=40)

    passing = client.get("/submissions", params={"decision": "PASS"}).text
    assert "Applicant 1" in passing
    assert "Applicant 2" not in passing


def test_detail_shows_criterion_scores_and_evidence(app_env, rubric):
    client, store, _ = app_env
    evaluate(store, rubric, "SUB-1", score=88)

    text = client.get("/submissions/SUB-1").text
    assert "Communication &amp; clarity" in text
    assert "said a thing" in text   # the evidence observation
    assert "00:10" in text          # its timestamp


def test_unknown_submission_is_a_404(app_env):
    client, _, _ = app_env
    assert client.get("/submissions/nope").status_code == 404


def test_reviewer_decision_is_recorded_and_wins(app_env, rubric):
    client, store, _ = app_env
    evaluate(store, rubric, "SUB-1", score=90)   # AI says PASS

    response = client.post(
        "/submissions/SUB-1/review",
        data={"decision": "NOT PASS", "reviewer": "hr@example.com", "notes": "Role mismatch."},
        follow_redirects=False,
    )
    assert response.status_code == 303

    submission = store.get("SUB-1")
    assert submission.reviewer_decision == "NOT PASS"
    assert submission.ai_decision == "PASS"
    assert submission.final_decision == "NOT PASS"
    assert submission.was_overridden
    assert submission.get("Reviewer") == "hr@example.com"


def test_overriding_the_ai_requires_a_note(app_env, rubric):
    client, store, _ = app_env
    evaluate(store, rubric, "SUB-1", score=90)   # AI says PASS

    response = client.post(
        "/submissions/SUB-1/review",
        data={"decision": "NOT PASS", "reviewer": "hr@example.com", "notes": "   "},
    )
    assert response.status_code == 400
    assert store.get("SUB-1").reviewer_decision == ""


def test_agreeing_with_the_ai_needs_no_note(app_env, rubric):
    client, store, _ = app_env
    evaluate(store, rubric, "SUB-1", score=90)

    client.post(
        "/submissions/SUB-1/review",
        data={"decision": "PASS", "reviewer": "hr@example.com", "notes": ""},
    )
    assert store.get("SUB-1").reviewer_decision == "PASS"
    assert not store.get("SUB-1").was_overridden


def test_a_reviewed_row_drops_out_of_the_pending_queue(app_env, rubric):
    client, store, _ = app_env
    assert len(store.pending()) == 2
    store.record_review(store.get("SUB-1"), review_columns("PASS", "hr@example.com"))
    assert [s.submission_id for s in store.pending()] == ["SUB-2"]


def test_upload_creates_a_pending_submission(app_env):
    client, store, uploads = app_env

    response = client.post(
        "/submit",
        data={"name": "Casey Rivera", "email": "casey@example.com", "role": "Ops", "answers": "Six years."},
        files={"video": ("intro.mp4", b"fake video bytes", "video/mp4")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    added = [s for s in store.all() if s.name == "Casey Rivera"]
    assert len(added) == 1
    submission = added[0]
    assert submission.status == "PENDING"
    assert submission.is_pending()
    assert (uploads / f"{submission.submission_id}.mp4").read_bytes() == b"fake video bytes"


def test_upload_rejects_a_non_video(app_env):
    client, store, _ = app_env
    before = len(store.all())

    response = client.post(
        "/submit",
        data={"name": "Casey"},
        files={"video": ("resume.pdf", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 400
    assert len(store.all()) == before


def test_uploaded_video_is_served_back(app_env):
    client, store, _ = app_env
    client.post(
        "/submit",
        data={"name": "Casey"},
        files={"video": ("intro.mp4", b"fake video bytes", "video/mp4")},
    )
    submission = next(s for s in store.all() if s.name == "Casey")

    response = client.get(f"/video/{submission.submission_id}")
    assert response.status_code == 200
    assert response.content == b"fake video bytes"


def test_video_route_refuses_a_path_outside_uploads(app_env, store_path=None):
    """A spreadsheet is editable by other people; a path in it is never trusted."""
    client, store, _ = app_env
    submission = store.get("SUB-1")
    store._write(submission, {"Video Link": "/etc/passwd"})

    assert client.get("/video/SUB-1").status_code == 404


def test_run_is_single_flight(app_env, monkeypatch):
    client, _, _ = app_env
    started = []

    def slow_pipeline(settings, store, rubric):
        class Blocked:
            def run(self):
                started.append(1)
                raise RuntimeError("boom")
        return Blocked()

    client.app.state.run_state.start()          # pretend a run is already going
    response = client.post("/run", follow_redirects=False)
    assert response.status_code == 303
    assert started == []                        # the second run never launched

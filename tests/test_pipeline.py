"""End-to-end run of the pipeline with the video and model layers faked out."""

import csv
from pathlib import Path

import pytest

from hr_agents import media
from hr_agents.pipeline import Pipeline
from hr_agents.store import CsvStore
from hr_agents.transcribe import Segment, StaticTranscriber, Transcript
from tests.conftest import make_result

HEADERS = ["Timestamp", "Submission ID", "Name", "Video Link"]


class FakeEvaluator:
    """Stands in for the Claude call. Records what it was asked to evaluate."""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def evaluate(self, submission_id, rubric, form_response, transcript, frames, duration):
        self.calls.append(
            {
                "submission_id": submission_id,
                "form_response": form_response,
                "transcript": transcript,
                "frames": frames,
                "duration": duration,
            }
        )
        outcome = self.results[submission_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def fake_media(monkeypatch, tmp_path):
    def fetch_video(link, dest_dir, drive_service=None):
        if "broken" in link:
            raise media.MediaError(f"Could not download {link}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = Path(dest_dir) / "video.mp4"
        path.write_bytes(b"fake")
        return path

    monkeypatch.setattr(media, "fetch_video", fetch_video)
    monkeypatch.setattr(media, "probe_duration", lambda path: 120.0)
    monkeypatch.setattr(
        media,
        "sample_frames",
        lambda path, count, duration=None: [
            media.Frame(timestamp_seconds=float(i * 20), jpeg=b"\xff\xd8jpeg") for i in range(count)
        ],
    )
    monkeypatch.setattr(media, "extract_audio", lambda path, dest: Path(dest) / "a.wav")


def build(tmp_path, rows, results, **kwargs):
    path = tmp_path / "s.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    evaluator = FakeEvaluator(results)
    transcript = Transcript(segments=(Segment(0.0, 4.0, "Hello, I'm Jane."),))
    pipeline = Pipeline(
        store=CsvStore(path),
        evaluator=evaluator,
        transcriber=StaticTranscriber(transcript),
        rubric=kwargs.pop("rubric"),
        workdir=tmp_path / "work",
        frame_count=kwargs.pop("frame_count", 3),
        **kwargs,
    )
    return pipeline, evaluator, path


def read_rows(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_a_passing_submission_is_scored_and_written_back(tmp_path, rubric, fake_media):
    rows = [{"Timestamp": "t", "Submission ID": "SUB-1", "Name": "Jane", "Video Link": "ok.mp4"}]
    pipeline, evaluator, path = build(
        tmp_path, rows, {"SUB-1": make_result(rubric, score=88)}, rubric=rubric
    )

    report = pipeline.run()

    assert report.counts == {"PASS": 1, "NOT PASS": 0, "NEEDS REVIEW": 0, "ERROR": 0}
    saved = read_rows(path)[0]
    assert saved["AI Score"] == "88"
    assert saved["AI Decision"] == "PASS"
    assert saved["Status"] == "DONE"
    assert saved["Confidence"] == "95"
    assert saved["Criterion Scores"].startswith("[")
    assert saved["Name"] == "Jane"  # form columns untouched


def test_the_evaluator_receives_frames_transcript_and_the_form_answers(tmp_path, rubric, fake_media):
    rows = [{"Timestamp": "t", "Submission ID": "SUB-1", "Name": "Jane", "Video Link": "ok.mp4"}]
    pipeline, evaluator, _ = build(
        tmp_path, rows, {"SUB-1": make_result(rubric)}, rubric=rubric, frame_count=4
    )

    pipeline.run()

    call = evaluator.calls[0]
    assert len(call["frames"]) == 4
    assert call["duration"] == 120.0
    assert call["form_response"] == {"Timestamp": "t", "Name": "Jane"}
    assert "Hello, I'm Jane." in call["transcript"].text


def test_one_bad_row_does_not_stop_the_batch(tmp_path, rubric, fake_media):
    rows = [
        {"Timestamp": "t", "Submission ID": "SUB-1", "Name": "A", "Video Link": "broken.mp4"},
        {"Timestamp": "t", "Submission ID": "SUB-2", "Name": "B", "Video Link": "ok.mp4"},
    ]
    pipeline, _, path = build(
        tmp_path, rows, {"SUB-2": make_result(rubric, score=50)}, rubric=rubric
    )

    report = pipeline.run()

    assert report.counts == {"PASS": 0, "NOT PASS": 1, "NEEDS REVIEW": 0, "ERROR": 1}
    first, second = read_rows(path)
    assert first["Status"] == "ERROR"
    assert "Could not download" in first["Error"]
    assert second["Status"] == "DONE"
    assert second["AI Decision"] == "NOT PASS"


def test_an_over_length_video_is_rejected_before_the_model_is_called(tmp_path, rubric, fake_media):
    rows = [{"Timestamp": "t", "Submission ID": "SUB-1", "Name": "A", "Video Link": "ok.mp4"}]
    pipeline, evaluator, path = build(
        tmp_path, rows, {}, rubric=rubric, max_video_seconds=60
    )

    report = pipeline.run()

    assert evaluator.calls == []
    assert report.counts["ERROR"] == 1
    assert "over the 60s limit" in read_rows(path)[0]["Error"]


def test_limit_caps_the_batch(tmp_path, rubric, fake_media):
    rows = [
        {"Timestamp": "t", "Submission ID": f"SUB-{i}", "Name": "A", "Video Link": "ok.mp4"}
        for i in range(3)
    ]
    results = {f"SUB-{i}": make_result(rubric) for i in range(3)}
    pipeline, evaluator, _ = build(tmp_path, rows, results, rubric=rubric)

    pipeline.run(limit=2)

    assert len(evaluator.calls) == 2


def test_scratch_files_are_cleaned_up(tmp_path, rubric, fake_media):
    rows = [{"Timestamp": "t", "Submission ID": "SUB-1", "Name": "A", "Video Link": "ok.mp4"}]
    pipeline, _, _ = build(tmp_path, rows, {"SUB-1": make_result(rubric)}, rubric=rubric)

    pipeline.run()

    assert not (tmp_path / "work" / "SUB-1").exists()

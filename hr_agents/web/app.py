"""The review app.

A thin surface over the same store and pipeline the CLI uses. Everything it
shows comes from the spreadsheet; everything a reviewer does goes back to the
spreadsheet. The app holds no state of its own beyond in-flight run status.
"""

from __future__ import annotations

import logging
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Settings
from ..evaluator import Evaluator
from ..pipeline import Pipeline
from ..rubric import Rubric, load_rubric
from ..store import (
    SUBMISSION_ID,
    VIDEO_LINK,
    CsvStore,
    GoogleSheetsStore,
    Submission,
    SubmissionStore,
    build_drive_service,
    review_columns,
)
from ..transcribe import FasterWhisperTranscriber
from .stats import summarize

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv"}


class RunState:
    """Tracks the one evaluation run the app allows at a time.

    One at a time is a deliberate limit, not an oversight: runs write to the same
    spreadsheet rows and a second concurrent run would race the first.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.message = ""
        self.finished_at: str | None = None

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return False
            self.running = True
            self.message = "Evaluating…"
            return True

    def finish(self, message: str) -> None:
        with self._lock:
            self.running = False
            self.message = message
            self.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_store(settings: Settings) -> SubmissionStore:
    if settings.store == "sheets":
        return GoogleSheetsStore(settings.spreadsheet_id, settings.worksheet)
    return CsvStore(settings.csv_path)


def build_pipeline(settings: Settings, store: SubmissionStore, rubric: Rubric) -> Pipeline:
    return Pipeline(
        store=store,
        evaluator=Evaluator(model=settings.model, effort=settings.effort),
        transcriber=FasterWhisperTranscriber(settings.whisper_model),
        rubric=rubric,
        workdir=settings.workdir,
        frame_count=settings.frame_count,
        max_video_seconds=settings.max_video_seconds,
        drive_service=build_drive_service() if settings.store == "sheets" else None,
    )


def create_app(
    settings: Settings | None = None,
    store: SubmissionStore | None = None,
    rubric: Rubric | None = None,
    pipeline_factory=build_pipeline,
    uploads_dir: Path | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    rubric = rubric or load_rubric(settings.rubric_path)
    store = store or build_store(settings)
    uploads = Path(uploads_dir or "uploads").resolve()
    uploads.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="HR-Agents", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    run_state = RunState()

    app.state.settings = settings
    app.state.store = store
    app.state.rubric = rubric
    app.state.uploads = uploads
    app.state.run_state = run_state

    def context(**extra) -> dict:
        """Values every page needs. Starlette adds `request` itself."""
        return {"rubric": rubric, "settings": settings, "run_state": run_state, **extra}

    def require(submission_id: str) -> Submission:
        submission = store.get(submission_id)
        if submission is None:
            raise HTTPException(status_code=404, detail=f"No submission {submission_id}")
        return submission

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        submissions = store.all()
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            context(
                stats=summarize(submissions),
                recent=sorted(submissions, key=lambda s: s.row_number, reverse=True)[:8],
            ),
        )

    @app.get("/submissions", response_class=HTMLResponse)
    def submissions_list(request: Request, decision: str = "", status: str = ""):
        submissions = store.all()
        if decision:
            submissions = [s for s in submissions if s.final_decision == decision]
        if status:
            submissions = [s for s in submissions if (s.status or "PENDING") == status]
        return templates.TemplateResponse(
            request,
            "submissions.html",
            context(
                submissions=sorted(submissions, key=lambda s: s.row_number, reverse=True),
                decision=decision,
                status=status,
            ),
        )

    @app.get("/submissions/{submission_id}", response_class=HTMLResponse)
    def submission_detail(request: Request, submission_id: str):
        submission = require(submission_id)
        return templates.TemplateResponse(
            request,
            "detail.html",
            context(
                submission=submission,
                video_url=_video_url(submission, uploads),
                criteria={c.id: c for c in rubric.criteria},
            ),
        )

    @app.post("/submissions/{submission_id}/review")
    def record_review(
        submission_id: str,
        decision: str = Form(...),
        reviewer: str = Form(...),
        notes: str = Form(""),
    ):
        submission = require(submission_id)
        if decision not in ("PASS", "NOT PASS"):
            raise HTTPException(status_code=400, detail="Decision must be PASS or NOT PASS.")
        if decision != submission.ai_decision and not notes.strip():
            raise HTTPException(
                status_code=400,
                detail="Overriding the AI decision requires a note explaining why.",
            )
        store.record_review(submission, review_columns(decision, reviewer.strip(), notes.strip()))
        return RedirectResponse(f"/submissions/{submission_id}", status_code=303)

    @app.get("/submit", response_class=HTMLResponse)
    def submit_form(request: Request):
        return templates.TemplateResponse(request, "submit.html", context())

    @app.post("/submit")
    async def submit(
        name: str = Form(...),
        email: str = Form(""),
        role: str = Form(""),
        answers: str = Form(""),
        video: UploadFile | None = None,
    ):
        if video is None or not video.filename:
            raise HTTPException(status_code=400, detail="A video file is required.")
        suffix = Path(video.filename).suffix.lower()
        if suffix not in VIDEO_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"{suffix or 'That file type'} is not a video. Accepted: "
                + ", ".join(sorted(VIDEO_SUFFIXES)),
            )

        submission_id = f"SUB-{uuid.uuid4().hex[:8]}"
        destination = uploads / f"{submission_id}{suffix}"
        with destination.open("wb") as handle:
            shutil.copyfileobj(video.file, handle)

        store.append(
            {
                "Timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                SUBMISSION_ID: submission_id,
                "Name": name.strip(),
                "Email": email.strip(),
                "Role": role.strip(),
                "Answers": answers.strip(),
                VIDEO_LINK: str(destination),
                "Status": "PENDING",
            }
        )
        return RedirectResponse(f"/submissions/{submission_id}", status_code=303)

    @app.post("/run")
    def run_pending(request: Request):
        if not run_state.start():
            return RedirectResponse("/", status_code=303)

        def work() -> None:
            try:
                report = pipeline_factory(settings, store, rubric).run()
                counts = report.counts
                run_state.finish(
                    f"{len(report.evaluated)} evaluated — {counts['PASS']} pass, "
                    f"{counts['NOT PASS']} not pass, {counts['NEEDS REVIEW']} needs review, "
                    f"{counts['ERROR']} error."
                )
            except Exception as exc:  # surfaced in the banner, not swallowed
                logger.exception("evaluation run failed")
                run_state.finish(f"Run failed: {type(exc).__name__}: {exc}")

        threading.Thread(target=work, daemon=True).start()
        return RedirectResponse("/", status_code=303)

    @app.get("/health")
    def health():
        return {"status": "ok", "rubric": rubric.name, "rubric_version": rubric.version}

    @app.get("/video/{submission_id}")
    def video(submission_id: str):
        from fastapi.responses import FileResponse

        submission = require(submission_id)
        path = _local_video_path(submission, uploads)
        if path is None:
            raise HTTPException(status_code=404, detail="No local video for this submission.")
        return FileResponse(path)

    return app


def _local_video_path(submission: Submission, uploads: Path) -> Path | None:
    """Resolve the video only if it sits inside the uploads directory.

    The Video Link column is data from a spreadsheet other people can edit, so it
    is never trusted as a filesystem path — a row reading `../../etc/passwd` must
    not turn this route into a file server.
    """
    link = submission.video_link
    if not link:
        return None
    try:
        candidate = Path(link).resolve()
        candidate.relative_to(uploads)
    except (ValueError, OSError):
        return None
    return candidate if candidate.is_file() else None


def _video_url(submission: Submission, uploads: Path) -> str | None:
    """A playable URL: our own route for uploads, the link itself for the web."""
    if _local_video_path(submission, uploads) is not None:
        return f"/video/{submission.submission_id}"
    link = submission.video_link
    if link.startswith(("http://", "https://")):
        return link
    return None

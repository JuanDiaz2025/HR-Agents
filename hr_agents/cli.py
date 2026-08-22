"""Command line entry point: `hr-agents run`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import ConfigError, Settings
from .evaluator import Evaluator
from .pipeline import Pipeline
from .rubric import RubricError, load_rubric
from .store import CsvStore, GoogleSheetsStore, build_drive_service
from .transcribe import FasterWhisperTranscriber


def build_pipeline(settings: Settings) -> Pipeline:
    rubric = load_rubric(settings.rubric_path)

    drive_service = None
    if settings.store == "sheets":
        store = GoogleSheetsStore(settings.spreadsheet_id, settings.worksheet)
        drive_service = build_drive_service()
    else:
        store = CsvStore(settings.csv_path)

    return Pipeline(
        store=store,
        evaluator=Evaluator(model=settings.model, effort=settings.effort),
        transcriber=FasterWhisperTranscriber(settings.whisper_model),
        rubric=rubric,
        workdir=settings.workdir,
        frame_count=settings.frame_count,
        max_video_seconds=settings.max_video_seconds,
        drive_service=drive_service,
    )


def _serve(settings, args) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "The app needs the web extra: pip install -e '.[web]'",
            file=sys.stderr,
        )
        return 2

    from .web.app import create_app

    app = create_app(settings=settings, uploads_dir=Path(args.uploads))
    print(f"HR-Agents review app on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hr-agents", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Evaluate every pending submission.")
    run.add_argument("--limit", type=int, help="Stop after this many submissions.")
    run.add_argument("-v", "--verbose", action="store_true")

    check = subparsers.add_parser("check", help="Validate the rubric and settings, then exit.")
    check.add_argument("-v", "--verbose", action="store_true")

    serve = subparsers.add_parser("serve", help="Run the review app.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--uploads", default="uploads", help="Where submitted videos are stored.")
    serve.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        settings = Settings.from_env()
        rubric = load_rubric(settings.rubric_path)
    except (ConfigError, RubricError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.command == "serve":
        return _serve(settings, args)

    if args.command == "check":
        print(f"Rubric '{rubric.name}' v{rubric.version}: {len(rubric.criteria)} criteria, "
              f"pass at {rubric.decision.pass_threshold}.")
        print(f"Model {settings.model} at effort {settings.effort}, store {settings.store}.")
        return 0

    report = build_pipeline(settings).run(limit=args.limit)
    counts = report.counts
    print(
        f"{len(report.evaluated)} evaluated — "
        f"{counts['PASS']} pass, {counts['NOT PASS']} not pass, "
        f"{counts['NEEDS REVIEW']} needs review, {counts['ERROR']} error."
    )
    for submission_id, message in report.failed:
        print(f"  ERROR {submission_id}: {message}", file=sys.stderr)
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

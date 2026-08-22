# HR-Agents

AI video evaluation & decision pipeline for job applicant screening. Applicants
fill out a form and submit a video; the service scores each video against a
configurable rubric, decides Pass / Not Pass with a confidence score, logs
everything back to the spreadsheet, and leaves the final call overridable by a
human reviewer.

## How it works

```
Form -> Spreadsheet row -> Video linked -> ffmpeg (frames + audio)
     -> transcript -> Claude scores the rubric -> weighted score + decision
     -> written back to the row -> human review -> reporting
```

Full detail in [`docs/architecture.md`](docs/architecture.md); the column
contract is in [`docs/spreadsheet_schema.md`](docs/spreadsheet_schema.md).

## Layout

```
hr_agents/rubric.py       Rubric loading and validation
hr_agents/models.py       Structured-output contract (Pydantic)
hr_agents/scoring.py      Weighted score + decision precedence
hr_agents/media.py        Video fetch, frame sampling, audio extraction
hr_agents/transcribe.py   Speech-to-text (faster-whisper by default)
hr_agents/evaluator.py    The Claude call
hr_agents/store.py        Google Sheets / CSV, behind one interface
hr_agents/pipeline.py     Stages 4-7, one row at a time
hr_agents/web/            The review app (FastAPI + server-rendered HTML)
hr_agents/cli.py          `hr-agents run` / `check` / `serve`

config/rubric.yaml        Criteria, weights, thresholds, prohibited factors
prompts/video_evaluation.md   System + user prompt templates
schemas/evaluation_result.json  Generated from models.py
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[web,sheets,transcribe,dev]'
cp .env.example .env        # then fill it in
```

ffmpeg must be on `PATH` — it does the frame sampling and audio extraction.

For the Google Sheets store, create a service account, download its JSON key,
point `GOOGLE_APPLICATION_CREDENTIALS` at it, and share both the spreadsheet and
the Drive folder holding the videos with the service account's email address.

## The app

```bash
python scripts/seed_demo.py demo.csv          # sample data, so you can see it working
HR_AGENTS_CSV_PATH=demo.csv hr-agents serve   # http://127.0.0.1:8000
```

Four pages, all reading and writing the same spreadsheet the CLI uses:

- **Dashboard** — pass rate, score distribution, decision breakdown, flags raised,
  and the override rate. Nothing is stored in the app itself.
- **Submissions** — the queue, filterable by decision or by error. Shows the AI's
  call and the final call side by side, marking the rows a human overturned.
- **Submission detail** — the video, every criterion score with its rationale and
  timestamped evidence, the written application, and the review form.
- **New submission** — stands in for the applicant-facing form so you can run the
  pipeline end to end before wiring up the real one.

Two rules the app enforces rather than suggests: overriding the AI requires a
written note, and a row a reviewer has decided is never re-evaluated.

`Evaluate pending` on the dashboard runs the pipeline in the background. One run
at a time — concurrent runs would race each other writing the same rows.

## Running from the command line

```bash
hr-agents check              # validate the rubric and settings, then exit
hr-agents run                # evaluate every pending row
hr-agents run --limit 5      # useful for a first pass against real data
```

A row is pending when it has a video link, no `Reviewer Decision`, and a
`Status` of blank, `PENDING`, or `ERROR`. Re-running is safe: rows are updated
in place by `Submission ID`, never appended, and a row a human has decided is
never touched again.

Start with `HR_AGENTS_STORE=csv` against a CSV export of the sheet — it exercises
the whole pipeline without write access to production data.

## Changing the rubric

Everything about how videos are scored lives in `config/rubric.yaml`: the
criteria, their weights and anchor descriptions, the pass and review thresholds,
and which flags force human review. Weights must sum to 100 or the pipeline
refuses to start. `hr-agents check` validates a change before you run a batch.

## Development

```bash
pytest                              # 62 tests, no network, no ffmpeg needed
python scripts/generate_schema.py   # after changing hr_agents/models.py
```

## Fairness

This pipeline screens people, so two things are load-bearing rather than
decoration:

- `prohibited_factors` in the rubric is injected verbatim into every evaluation
  prompt, and the model is instructed that none of it may influence any score.
- The `Reviewer Decision` column always wins, and low-confidence cases are routed
  to a human instead of auto-decided.

Watch the override rate — reviewers overturning the AI regularly means the rubric
is wrong, not that the reviewers are. Before this touches real applicants, run it
against a set of past submissions with known outcomes and compare.

# AI Video Evaluation & Decision Pipeline — Architecture

Evaluate videos submitted by people who filled out the intake form, and return a
Pass / Not Pass decision with a score, confidence, and written feedback.

## Pipeline

| # | Stage | What happens | Owner |
|---|-------|--------------|-------|
| 1 | Form submission | Applicant fills out the intake form | Form platform |
| 2 | Data capture | Response is written as a row in the spreadsheet | Form -> Sheet |
| 3 | Video submission | Applicant uploads a video, tied to their response | Form / upload link |
| 4 | Data integration | Video file is linked to the spreadsheet row | Integration layer |
| 5 | AI evaluation | Video is analyzed against the rubric in `config/rubric.yaml` | Evaluator |
| 6 | AI decision | Weighted score -> PASS / NOT PASS + confidence (0-100) | Evaluator |
| 7 | Results logged | Score, decision, confidence, feedback written back to the row | Integration layer |
| 8 | Review & override | Human reviewer can overturn the AI decision | Reviewer |
| 9 | Reporting | Pass rate, score distribution, trends | Dashboard |

## Design rules

- **The spreadsheet is the single source of truth.** Every stage reads and writes
  the same row. Nothing lives only in the evaluator.
- **Idempotent per row.** A row is keyed by submission ID; re-running an
  evaluation overwrites that row's AI columns and never duplicates a row.
- **Never overwrite a human.** Once `Reviewer Decision` is set, the AI columns
  are advisory only and the final decision is the reviewer's.
- **The rubric is data, not code.** Criteria, weights, and the pass threshold
  live in `config/rubric.yaml` so HR can change them without a deploy.
- **Every decision is explainable.** The evaluator must return per-criterion
  scores and evidence, not just a verdict.
- **Low confidence routes to a human.** Below `review_threshold`, the row is
  flagged `NEEDS REVIEW` rather than auto-decided.

## Stage 5 in detail

1. Fetch the video (`hr_agents/media.py`) — a local path, an HTTP URL, or a
   Google Drive share link.
2. Sample N frames evenly across the video and extract 16 kHz mono audio, both
   via ffmpeg. Transcribe the audio to timestamped segments
   (`hr_agents/transcribe.py`).
3. Send the rubric (system prompt, cached) plus the frames, transcript, and form
   response (user turn) to Claude — `hr_agents/evaluator.py`, template in
   `prompts/video_evaluation.md`.
4. The response is constrained to `schemas/evaluation_result.json` via structured
   outputs, so it is a validated object rather than parsed prose.
5. Compute the weighted score and the decision locally (`hr_agents/scoring.py`) —
   the model scores criteria, the code applies the threshold. This keeps the pass
   bar deterministic and auditable.

### Why frames rather than the whole video

A screening video is a person talking to a camera. Nearly all the visual signal
the rubric asks about — is this the applicant, are they prepared, is the setting
appropriate — survives sampling. Six stills plus a full transcript costs a small
fraction of streaming the video, and the prompt tells the model explicitly not
to infer anything about the moments between frames.

## Decision precedence

`hr_agents/scoring.py` applies these in order, and the first match wins:

1. A flag in `decision.flags_requiring_review` -> `NEEDS REVIEW`.
2. Model confidence below `decision.review_threshold` -> `NEEDS REVIEW`.
3. A criterion marked `compliance: true` scoring 0 -> `NOT PASS`.
4. Weighted score against `decision.pass_threshold` -> `PASS` / `NOT PASS`.

Confidence outranks the compliance auto-fail deliberately: if the model is not
sure what it saw, a human should be the one to fail someone.

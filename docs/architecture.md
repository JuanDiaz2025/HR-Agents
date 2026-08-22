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

1. Fetch the video from its storage location.
2. Produce the analysis inputs (transcript and/or sampled frames — see the
   open decision in the README).
3. Send the rubric + inputs to the model with `prompts/video_evaluation.md`.
4. Validate the response against `schemas/evaluation_result.json`. Retry once on
   a schema failure; on a second failure, write `ERROR` and flag for review.
5. Compute the weighted score and the decision locally in code — the model
   scores the criteria, the code applies the threshold. This keeps the pass bar
   deterministic and auditable.

# HR-Agents

AI video evaluation & decision pipeline: applicants fill out a form and submit a
video; the system scores the video against a configurable rubric, decides
Pass / Not Pass with a confidence score, logs everything back to the
spreadsheet, and lets a human reviewer override.

## Status

Foundation in place. The runtime is not yet built — two decisions are pending
(see **Open decisions**).

## Layout

```
config/rubric.yaml            Criteria, weights, thresholds, prohibited factors
prompts/video_evaluation.md   Stage 5 prompt template
schemas/evaluation_result.json  Contract the model output must satisfy
docs/architecture.md          The 9-stage pipeline and its design rules
docs/spreadsheet_schema.md    Column-by-column spec, ownership, invariants
```

## Open decisions

**1. Where the pipeline runs**
- Google Apps Script bound to the Sheet — no infrastructure, triggers on form
  submit, but limited runtime and awkward to test.
- Python service in this repo — full control, testable, needs somewhere to run
  (Cloud Run / scheduled job) and Sheets API credentials.
- Existing automation platform (n8n / Make / Zapier) orchestrating an API call.

**2. How the video is analyzed**
- Transcript only (speech-to-text, then text evaluation) — cheapest, fastest,
  blind to anything visual.
- Multimodal (transcript + sampled frames or native video input) — can assess
  presentation and on-camera behavior, costs more per submission.

## Fairness note

`config/rubric.yaml` carries a `prohibited_factors` list that is injected into
every evaluation prompt. Because this pipeline screens people, that list and the
`Reviewer Decision` override path are load-bearing, not decoration. The override
rate is the metric to watch: if reviewers routinely overturn the AI, the rubric
is wrong.

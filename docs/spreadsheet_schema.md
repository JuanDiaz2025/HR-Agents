# Spreadsheet Schema (single source of truth)

One row per submission. Columns are grouped by who writes them.

## Written by the form (stages 1-3)

| Column | Type | Notes |
|---|---|---|
| `Timestamp` | datetime | Form submission time |
| `Submission ID` | string | Stable unique key. Generated if the form does not supply one. |
| `Name` | string | |
| `Email` | string | |
| `<form fields...>` | mixed | One column per question on the form |
| `Video Link` | url | Set at stage 4 when the upload is matched to the row |

## Written by the pipeline (stages 4-7)

| Column | Type | Notes |
|---|---|---|
| `Status` | enum | `PENDING`, `PROCESSING`, `DONE`, `ERROR` |
| `AI Score` | int 0-100 | Weighted total computed in code from the criterion scores |
| `AI Decision` | enum | `PASS`, `NOT PASS`, `NEEDS REVIEW` |
| `Confidence` | int 0-100 | From the model |
| `Feedback` | string | Short applicant-facing summary |
| `Strengths` | string | Semicolon-separated |
| `Areas to Improve` | string | Semicolon-separated |
| `Flags` | string | Semicolon-separated schema flags |
| `Criterion Scores` | json | Per-criterion scores + rationale + evidence, for audit |
| `Rubric Version` | int | Which rubric version produced this result |
| `Evaluated At` | datetime | |
| `Error` | string | Populated only when `Status = ERROR` |

## Written by the human reviewer (stage 8)

| Column | Type | Notes |
|---|---|---|
| `Reviewer` | string | Email of the reviewer |
| `Reviewer Decision` | enum | `PASS`, `NOT PASS` — blank until reviewed |
| `Reviewer Notes` | string | Required when the reviewer overrides the AI |
| `Reviewed At` | datetime | |

## Derived (stage 9)

`Final Decision` = `Reviewer Decision` when set, otherwise `AI Decision`.
Dashboards should report on `Final Decision`, and track the override rate
(`Reviewer Decision` != `AI Decision`) as the primary quality metric for the
rubric.

## Invariants

- The pipeline writes only to its own column group. It never modifies form
  columns or reviewer columns.
- Rows with a non-empty `Reviewer Decision` are skipped on re-runs unless
  explicitly re-queued.
- `Submission ID` is the idempotency key: re-evaluating a row overwrites the
  pipeline columns in place and never appends a new row.

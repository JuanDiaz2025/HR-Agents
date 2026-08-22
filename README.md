# HR-Agents — AI Interviewer for Google Meet

An AI interviewer that joins a Google Meet call, conducts a structured screening
conversation by voice, scores the candidate against a rubric, and pushes the
result into Google Sheets and monday.com.

```
 candidate books ──► Google Calendar ──► Recall.ai bot scheduled
                        (Meet link)              │
                                                 ▼
                              ┌──────────── live call ────────────┐
                              │  bot audio ──► STT ──► Claude     │
                              │       ▲                  │        │
                              │       └──── TTS ◄────────┘        │
                              └───────────────┬───────────────────┘
                                              ▼
                     score (LLM) ──► rules (code) ──► Sheets ──► monday.com
                                                              └─► Slack / email
```

Everything the AI knows lives in three editable YAML files — the question bank,
the scoring rubric, and the disqualification rules. No prompt engineering is
needed to change what it asks or how it scores.

## What is where

| Path | What it does |
|---|---|
| `knowledge/questions.yaml` | Question bank per role, opening/closing scripts |
| `knowledge/rubric.yaml` | Scoring categories, weights, anchors, PROCEED/REVIEW thresholds |
| `knowledge/policies.yaml` | Company facts, FAQ, guardrails, hard disqualification rules |
| `app/interview/engine.py` | The interview state machine — turn-taking, follow-ups, early exit |
| `app/interview/scoring.py` | Deterministic scoring: weights, thresholds, rule evaluation |
| `app/clients/llm.py` | The Claude brain (per-turn decisions, scoring, fact extraction) |
| `app/clients/recall.py` | Recall.ai: schedule the bot, make it speak |
| `app/clients/google_calendar.py` | Create the event, generate the Meet link |
| `app/clients/sheets.py`, `monday.py` | Result sinks |
| `app/api/webhooks.py` | Inbound Recall.ai traffic (bot lifecycle + live transcript) |
| `app/services/pipeline.py` | Post-call: score → publish → notify |
| `scripts/simulate_interview.py` | Full interview offline, no accounts needed |

**Setup instructions — accounts, keys, Google Workspace delegation, going
live — are in [SETUP.md](SETUP.md).**

## Try it with no accounts at all

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

python scripts/simulate_interview.py                     # scripted candidate
python scripts/simulate_interview.py --scenario weak
python scripts/simulate_interview.py --scenario disqualified
python scripts/simulate_interview.py --interactive        # you answer
pytest -q
```

With no `ANTHROPIC_API_KEY` the simulator plays back each scenario's
hand-written facts and scores through the **real** scoring, rules and
publication code — so the deterministic half of the system is genuinely
exercised, and you can watch a disqualified candidate get closed out early
(the `weak` and `disqualified` scenarios stop at 7 questions instead of 11).
What is stubbed is the model's judgement: follow-up decisions and the actual
evaluation of what was said. Set the key and those turn on.

## Run the service

```bash
cp .env.example .env          # then fill it in — see SETUP.md
uvicorn app.main:app --reload --port 8000
curl -s localhost:8000/health/integrations | python -m json.tool
```

`/health/integrations` reports which integrations are live and which are running
as logged no-ops. Check it after every deploy — a missing key does not crash the
app, it silently does nothing.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/bookings` | Book an interview (Calendar event + scheduled bot) |
| `DELETE` | `/api/bookings/{id}` | Cancel: deletes the bot and the event |
| `GET` | `/api/interviews` | List, filter by `?status=` |
| `GET` | `/api/interviews/{id}` | Detail incl. scorecard |
| `GET` | `/api/interviews/{id}/transcript` | JSON transcript |
| `GET` | `/api/interviews/{id}/transcript.txt` | Plain-text transcript |
| `POST` | `/api/interviews/{id}/rescore` | Re-score and re-publish (after a rubric edit) |
| `POST` | `/api/webhooks/recall/status` | Recall bot lifecycle (Svix-signed) |
| `POST` | `/api/webhooks/recall/realtime/` | Live transcript + participant events |
| `WS` | `/api/ws/recall/` | Same events over a websocket |
| `GET` | `/agent/{id}` | Agent webpage, for `output_media` mode |
| `GET` | `/health`, `/health/integrations` | Liveness and wiring |

Our own routes take `X-API-Key: $INTERNAL_API_KEY`. Recall's routes are verified
by HMAC signature (`RECALL_WEBHOOK_SECRET`) or a URL token, never by that key.

Book an interview:

```bash
curl -X POST localhost:8000/api/bookings \
  -H "X-API-Key: $INTERNAL_API_KEY" -H 'Content-Type: application/json' \
  -d '{"full_name":"Ana Santos","email":"ana@example.com",
       "scheduled_at":"2026-09-01T14:00:00+08:00","role":"virtual_assistant"}'
```

## How the interview actually works

1. **Bot joins and starts recording** → `bot.in_call_recording` webhook → the AI
   speaks the opening from `questions.yaml`.
2. **Candidate speaks** → Recall streams finalised utterances to
   `/api/webhooks/recall/realtime/`. Utterances are buffered and a silence timer
   (`ANSWER_DEBOUNCE_S`) is re-armed on each one, so the AI does not interrupt
   someone pausing mid-sentence.
3. **Silence** → one Claude call decides: follow up, move on, answer a question
   the candidate asked, or close. The system prompt is per-role and
   byte-stable, so it is prompt-cached across every turn.
4. **After each availability question** the facts so far are extracted and run
   through the disqualification rules. A confirmed hard blocker closes the
   interview politely rather than spending the full slot.
5. **Call ends** → `bot.done` → one scoring call produces category scores and
   extracted facts → `scoring.py` applies weights, thresholds and rules → row
   written to Sheets, item created or moved on monday.com, Slack ping, and an
   email only when a human decision is actually needed.

Guardrails the model does not get to override, enforced in `engine.py`:
follow-up caps, question caps, the wall-clock limit, and the disqualification
rules themselves. Claude supplies scores and judgement; the outcome is computed
in Python so it is reproducible and auditable.

### Two ways the bot can talk

- **`output_audio`** (default) — server-side TTS, mp3 posted to Recall. Simple
  and reliable; latency is one TTS round trip plus one API call. Requires
  `automatic_audio_output` at bot-create time, which the client always sets.
- **`output_media`** — Recall streams a webpage we host (`/agent/{id}`) which
  speaks via the browser. Lower latency and gives the bot a camera feed, at the
  cost of a browser in the loop. The bundled page uses the browser's own speech
  synthesis; swap it for an ElevenLabs fetch for production voice quality.

## Editing what it asks and how it scores

Edit the YAML and restart. The loader validates on boot and refuses to start on
a question whose category is not in the rubric, or a rule referencing a fact
that is never extracted — the failure modes that otherwise silently disable a
rule. To add a role, copy the `virtual_assistant` block in `questions.yaml`,
rename it, and pass that name as `role` when booking.

After changing the rubric, `POST /api/interviews/{id}/rescore` re-scores past
interviews against it.

## Scaling and limits

- **Run one worker.** Per-call timing state (the silence debounce, the
  "am I speaking" window) is in-process. Durable state is all in the database,
  so a restart mid-call loses only the debounce timer — but two workers would
  split the state for one call. Move `InterviewEngine._runtimes` to Redis before
  scaling out.
- **SQLite** is the default and holds up for a few hundred interviews. Switch
  `DATABASE_URL` to Postgres before you need concurrency.
- **Schedule bots more than 10 minutes ahead.** Inside that window Recall hands
  out ad-hoc bots from a finite warm pool and can return 507; the client retries
  per Recall's guidance, but a scheduled bot is guaranteed to join on time.
- **Costs, per 30-minute interview** (order of magnitude, not a quote): Recall.ai
  bot minutes are the largest line, then TTS, then Claude — roughly 10–20 turn
  calls at low effort plus one scoring call at high effort. Check current
  Recall.ai and Anthropic pricing pages before committing to volume.

## Legal and ethical notes

This system records people, transcribes them, and produces an automated
assessment that affects their employment. Before running it on real candidates:

- **Tell candidates.** The opening script discloses that it is an AI and that
  the call is recorded — keep that. Two-party consent applies in many
  jurisdictions, and the Philippines' Data Privacy Act plus (for US-facing
  hiring) state laws such as Illinois' AI Video Interview Act impose notice and
  consent duties. NYC Local Law 144 requires a bias audit and candidate notice
  for automated employment decision tools.
- **Keep a human in the loop.** The rubric routes borderline candidates to
  `REVIEW` for exactly this reason. Do not let `DO_NOT_PROCEED` send a rejection
  automatically without a human looking first.
- **Review the guardrails** in `policies.yaml`. They already forbid asking about
  protected characteristics and instruct scoring on clarity rather than accent.
  Audit real transcripts for accent or dialect bias in scoring before you rely
  on it at volume.
- **Set retention.** Nothing here deletes transcripts. Decide how long you keep
  them and add a job that enforces it.

None of that is legal advice — check your own obligations for the jurisdictions
you hire in.

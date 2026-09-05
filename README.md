# AI Sales Practice & Testing System

An AI seller persona calls your sales line, a rep answers and works the call, and
the system records it, transcribes it, scores it against your sales standard, and
writes a coaching report.

Real calls. Real scenarios. Real coaching.

```
  persona (Larry)  ->  Twilio call  ->  rep answers  ->  recording
                                                            |
                            report.html  <-  scorecard  <-  transcript
```

## Quick start

```bash
npm install
npm run demo -- --seed 12   # fill it with sample calls so there is something to look at
npm run serve               # then open http://localhost:3000
```

No credentials, no build step, no database. `rm -rf data/` clears the sample
data whenever you want.

```bash
npm run demo      # the loop on a single sample call, printed to the terminal
npm run doctor    # which phases are live, and what each missing one needs
```

`npm run demo` writes a real scorecard and a real `report.html` to
`data/sessions/<id>/`. That is the same code path a live call takes, so you can
see the output before you spend a dollar on telephony.

The hour-by-hour version, with exit criteria for each phase, is in
[`docs/DAY1-RUNBOOK.md`](docs/DAY1-RUNBOOK.md).

## The dashboard

`npm run serve` starts the web app at `http://localhost:3000`:

- **Dashboard** - team average, pass rate, and the categories costing the most
  weighted points per call
- **Reps** - score per call over time, whether each rep is improving, and the
  category costing them the most
- **Calls** - every session, click through to the scorecard and full report
- **Sellers** - the personas and how much each one holds back
- **Start a call** - launch a practice call: pick the rep, the seller and the
  difficulty
- **Setup** - the same capability check as `npm run doctor`

**One security note.** Once `PUBLIC_URL` is set, the dashboard is reachable by
anyone with that link. Set `DASHBOARD_TOKEN` in `.env` and open the dashboard
with `?token=...`. Until you do, starting calls from the web UI is refused
outright - otherwise a stranger who found the ngrok URL could spend your call
budget. Twilio's own webhooks are never behind that check, so calls keep working.

## The seven phases, and the command for each

| Phase | What it does | Command |
| --- | --- | --- |
| 1. Setup | Check what is wired up | `npm run doctor` |
| 2. Pull & analyze real calls | Transcribe your recordings, mine objections, seller language, win/loss patterns | `npm run mine -- ./recordings --apply-to larry` |
| 3. Build the AI seller | Persona -> voice instructions; practice by typing before you touch the phone | `node src/cli.js persona larry`<br>`node src/cli.js simulate` |
| 4. Connect & test call | AI seller dials your sales line, holds a real voice conversation | `npm run serve` then `npm run call -- --to +1...` |
| 5. Evaluate & score | Transcribe, grade against the rubric, write the coaching report | automatic after a call, or `npm run score -- <session>` |
| 6. Refine & retest | Change difficulty, re-run, compare | `npm run call -- --difficulty hard` |
| 7. Deliver | Reports as HTML and Markdown, one folder per session | `data/sessions/<id>/report.html` |

Everything the dashboard shows is also available from the CLI, and vice versa -
they share one code path, so starting a call from the web UI creates exactly the
session `npm run call` would.

## What each phase needs from you

Nothing here is a stub - every path is implemented. These are the accounts and
numbers only you can supply:

| To do this | You need |
| --- | --- |
| Mine real calls | `OPENAI_API_KEY`, plus your recordings in a folder |
| Score with an LLM | `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` |
| Typed practice (`simulate`) | `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` |
| Live phone calls | Twilio account + a Twilio number, `OPENAI_API_KEY`, and a public https URL (`ngrok http 3000` is fine for day one) |
| Nothing at all | `npm run demo`, the offline scorer, and the report renderer still work |

Copy `.env.example` to `.env` and fill in what you have. Anything you leave blank
degrades to the next-best path instead of failing: no LLM key falls back to the
heuristic scorer, a failed recording download falls back to the transcript the
realtime bridge captured live.

## The AI seller

`config/personas/larry.json` defines the character - the property, the situation,
the objections, and, importantly, the **reveal rules**: facts Larry only says when
the rep asks the right question. Larry's sister is on title, and a rep who never
asks who else is involved never hears about her. That is what makes the score
mean something.

Difficulty (`easy` / `medium` / `hard`) changes how much he volunteers, how often
he objects, and what it takes to get a next step out of him.

Add another seller by dropping a new JSON file next to `larry.json`.

## The sales standard

`config/scoring-rubric.json` is the standard reps are graded against: nine
weighted categories summing to 100, each with criteria and the auto-fails that
sink a call regardless of score (a number quoted before motivation is uncovered,
a call that ends with no next step).

Edit that file to change the standard. The scorer, the report, and the tests all
read it - there is no scoring logic hardcoded anywhere else.

## Running a live practice call

```bash
ngrok http 3000                 # copy the https URL into PUBLIC_URL in .env
npm run serve                   # terminal 1
npm run call -- --to +14155550134 --rep "Marcus" --difficulty hard   # terminal 2
```

Your sales line rings. A rep answers and works the call. When it ends, the server
downloads the recording, transcribes both channels separately for real speaker
labels, scores it, and writes the report - no further commands.

## Layout

```
src/
  cli.js              every command
  web/                the dashboard - plain HTML, CSS and JS, no build step
  web-routes.js       the dashboard's JSON API and static file serving
  analytics.js        rolls session folders up into team and per-rep numbers
  launch-call.js      the one code path that places a call (CLI and web share it)
  seed.js             sample sessions so the dashboard has something to show
  server.js           Twilio webhooks + the media-stream upgrade
  realtime-bridge.js  Twilio audio <-> OpenAI Realtime, both directions
  persona.js          persona JSON -> voice instructions
  twilio.js           outbound call, recording download, signature verification
  transcribe.js       Whisper, with per-channel speaker separation
  wav.js              dual-channel WAV splitting
  score.js            rubric scoring - LLM, with an offline fallback
  report.js           report.html + report.md
  mine-calls.js       phase 2: mine real recordings for insights
  store.js            one folder per session on disk
config/               the persona and the rubric - edit these, not the code
data/sessions/<id>/   session.json, recording.wav, transcript.json, scorecard.json, report.html
```

## Tests

```bash
npm test
```

Covers the WAV splitter, persona construction, transcript stats, the scorer's
weighting and clamping, auto-fail detection, HTML escaping, and the mining pass.
No network, no credentials.

## Cost, roughly

A ten-minute practice call is a few dollars of realtime voice, a few cents of
transcription, and a few cents of scoring. The offline scorer and the report cost
nothing. Watch the realtime voice line first - it dominates.

## Next scenarios

The persona format is not specific to Larry, or to sellers. The same loop runs for
inherited, foreclosure, landlord, and agent personas, and the same rubric shape
handles a different standard per scenario.

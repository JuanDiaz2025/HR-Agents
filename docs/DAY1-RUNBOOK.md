# Day One Runbook

The deployment plan, turned into commands. Times are the plan's; the work is
mostly waiting on accounts, not on code.

---

## 1. Kickoff & setup - 8:00 to 9:00

Owners: Jonathan (setup), Seth (infrastructure)

```bash
git clone <this repo> && cd HR-Agents
npm install
cp .env.example .env
npm run doctor          # everything will read "missing" - that is the checklist
npm run demo            # proves the scoring and reporting half works right now
```

Accounts to have open by 9:00:

- **OpenAI** - API key with Realtime access enabled (voice + transcription)
- **Anthropic** *(optional)* - for scoring; OpenAI can grade instead
- **Twilio** - account SID, auth token, and a voice-capable phone number
- **ngrok** (or any tunnel) - a public https URL for Twilio's webhooks
- The **sales line number** the AI seller should dial

Fill those into `.env`. Re-run `npm run doctor` until the rows you need read
`[ready]`.

**Exit criteria:** `npm run demo` produces a report, and `doctor` shows what is
still missing.

---

## 2. Pull & analyze real calls - 9:00 to 10:30

Owner: Jonathan

Drop 10-20 real recordings into a folder. Label the outcomes either by filename
prefix (`win-jones-0412.wav`, `loss-carter-0331.wav`) or with a `manifest.json`
in the same folder:

```json
{ "jones-0412.wav": "win", "carter-0331.wav": "loss" }
```

```bash
npm run mine -- ./recordings --apply-to larry
```

This transcribes each call (caching transcripts so a re-run is free), extracts
objections with their frequency, the phrases sellers actually use, the questions
that opened people up, and what the winning calls did differently. Results land
in `data/insights.json`; `--apply-to` folds the real objections and language into
the persona and bumps its version.

**Exit criteria:** `data/insights.json` exists, and `config/personas/larry.json`
is at v2 with mined objections in it.

**Note on privacy:** recordings and transcripts stay on the machine you run this
on. `data/` and audio files are gitignored. Only the audio you mine is sent to
the transcription API - decide deliberately whether your recordings can leave
your network before running this on real customer calls.

---

## 3. Build the AI seller - 10:30 to 12:00

Owner: Seth

```bash
node src/cli.js persona larry --difficulty hard   # read the actual instructions
node src/cli.js simulate --difficulty medium      # practice by typing, no phone
```

`simulate` is the fastest feedback loop in the system: it runs the same persona,
the same difficulty rules, and the same scorer, without touching telephony. Use it
to tune the character before spending money on voice minutes.

Tune these in `config/personas/larry.json`:

- `reveal_rules` - what Larry only says when asked. This is where the difficulty
  really lives. If reps are scoring high without earning it, tighten these.
- `objections` - swap in the real lines from `data/insights.json`.
- `personality.traits` - how he deflects, when he warms up.
- `voice` - the OpenAI realtime voice.

**Exit criteria:** a typed practice call feels like a real seller, and the
scorecard afterwards points at real gaps.

---

## 4. Connect & test call - 12:00 to 1:00

Owners: Jonathan & Seth

Terminal 1:

```bash
ngrok http 3000            # copy the https URL into PUBLIC_URL in .env
npm run serve
```

Terminal 2:

```bash
npm run call -- --to +14155550134 --rep "Marcus" --difficulty medium
```

The sales line rings. Answer it and work the call like a real lead.

What to watch on the server log: the live transcript prints as it goes, so you can
see whether Larry is holding character and whether barge-in is working.

If the call connects but nobody speaks, check `OPENAI_API_KEY` has Realtime
access. If Twilio reports a webhook error, `PUBLIC_URL` is wrong or the tunnel
died.

**Exit criteria:** a natural back-and-forth conversation, recorded, with
`data/sessions/<id>/recording.wav` on disk afterwards.

---

## 5. Evaluate & score - 1:00 to 2:00

Owner: Jonathan

This runs automatically when the call ends. To re-run any part by hand:

```bash
node src/cli.js sessions
npm run transcribe -- <session-id>
npm run score -- <session-id>
npm run report -- <session-id>
open data/sessions/<session-id>/report.html
```

Read the scorecard against the call you just had. If a category scored high for a
call you thought was weak (or the reverse), the fix belongs in
`config/scoring-rubric.json` - adjust the criteria, the weight, or the auto-fails,
then re-run `npm run score` on the same session and compare. Scoring is cheap and
repeatable; that is the point of keeping the transcript on disk.

**Exit criteria:** a scorecard the team agrees reflects the call, and a report
worth sending to a rep.

---

## 6. Refine & retest - 2:00 to 4:00

Owners: Jonathan & Seth

```bash
npm run call -- --difficulty hard --rep "Seth"
npm run call -- --difficulty easy --rep "new hire"
```

Loop: run a call, read the report, adjust `reveal_rules` or the rubric, run
another. Validate that the score moves when the behavior moves - run the same
scenario twice, once deliberately skipping the decision-maker question and once
asking it, and confirm the scorecard notices.

**Exit criteria:** three or more calls run, scoring that the team trusts, and a
difficulty setting that is neither trivial nor impossible.

---

## 7. Finalize & deliver - 4:00 to 5:00

Owners: Jonathan & Seth

Confirm the full loop end to end:

- [ ] AI seller calls the sales line
- [ ] Real conversation, natural, no script hints
- [ ] Recording and transcript saved
- [ ] Score and coaching report generated
- [ ] Ready for team practice

```bash
node src/cli.js sessions        # every call, its status, its score
git add -A && git commit -m "Day 1: working practice loop with tuned persona and rubric"
```

---

## Day 2 and beyond

- **More personas.** Copy `larry.json`: inherited, foreclosure, landlord, agent,
  tired-of-tenants. Each is a JSON file, no code.
- **Per-scenario rubrics.** `RUBRIC_PATH` selects the standard, so a foreclosure
  call can be graded differently from a landlord call.
- **Regular practice schedule.** `npm run call` is a single command - put it on a
  cron with a rep name and a rotating persona, two or three calls per rep per week.
- **Unannounced tests.** The same command, unscheduled, into the normal queue.
- **Rep trends.** Every session is a folder with a JSON scorecard. Aggregating
  score-by-category over time per rep is a reporting job on data you already have.
- **Text and email scenarios.** `buildInstructions()` is channel-agnostic - the
  persona already works for a non-voice channel.

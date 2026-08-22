# Setup — what you need to do to make this run

The code is complete. What remains is account setup, credentials, and one
deployment. Everything below is work only a human with admin access can do; the
app cannot provision any of it for you.

**Order matters.** Steps 1–4 get you a working interview. Steps 5–7 add the
automation. Step 8 is the go-live checklist.

Time: about 3–4 hours of setup, plus 1–2 days waiting on Google Workspace
domain-wide delegation if your admin is slow.

---

## Quick reference: what you are signing up for

| Service | Needed for | Cost model | Free trial? |
|---|---|---|---|
| **Recall.ai** | The bot that joins Google Meet | Per bot-hour | Yes |
| **Anthropic (Claude)** | Interview decisions + scoring | Per token | Credit on signup |
| **Google Workspace** | Calendar events + Meet links + Sheets | Per seat/month | Yes |
| **ElevenLabs** *(or OpenAI TTS)* | The AI's voice | Per character | Yes |
| **monday.com** | Candidate pipeline | Per seat/month | Existing account? |
| **A host** (Railway / Render / Fly / a VPS) | Running this service | ~$5–20/month | Usually |

You can skip ElevenLabs (use OpenAI TTS or Google TTS), monday.com, and Sheets
and still have a working interviewer. You cannot skip Recall.ai, Claude, Google
Workspace, or the host.

---

## Step 0 — Run it locally first, with zero accounts

Prove the machinery works before you spend anything.

```bash
git clone <this repo> && cd HR-Agents
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

pytest -q                                     # 109 tests, no credentials needed
python scripts/simulate_interview.py          # a full interview, printed
python scripts/simulate_interview.py --interactive   # you play the candidate
```

Then read `knowledge/questions.yaml` and **rewrite the questions for your actual
role**. This is the highest-leverage thing you will do — the questions are what
the whole system is built to ask. Do it before wiring anything up. Re-run the
simulator after editing; it validates the YAML on load.

---

## Step 1 — Google Workspace: the interviewer identity

You need a Workspace account (not a free @gmail.com — free accounts cannot do
domain-wide delegation, and the calendar/Meet API path depends on it).

1. **Create a dedicated mailbox** in the Admin console, e.g.
   `interviewer@yourdomain.com`. This account owns the interview events. Give it
   a real name and photo — candidates will see it.
2. **Enable Google Meet** for that user (Admin console → Apps → Google Workspace
   → Google Meet → ON).
3. **Enable Meet video calls for Calendar**: Admin console → Apps → Google
   Workspace → Calendar → Sharing settings → make sure video calls are allowed
   and *"Automatically add video calls to events users create"* is available.

### Create the service account

4. Go to <https://console.cloud.google.com/> → create a project (e.g.
   `ai-interviewer`).
5. **APIs & Services → Library** → enable **Google Calendar API** and **Google
   Sheets API**. (Also **Cloud Text-to-Speech API** if you plan to use Google
   for the voice.)
6. **APIs & Services → Credentials → Create credentials → Service account.**
   Name it `ai-interviewer-sa`. Skip the optional role grants.
7. Open the service account → **Keys → Add key → Create new key → JSON.**
   Download it. Save it to `secrets/service-account.json` in this repo — the
   `secrets/` directory is already gitignored. **Never commit this file.**
8. On the service account's **Details** tab, copy the **Unique ID** (a long
   number). This is its "client ID".

### Grant domain-wide delegation

This is the step that trips people up. A bare service account cannot create a
Meet conference or send an invitation; it has to act *as* your interviewer
mailbox.

9. Google Admin console → **Security → Access and data control → API controls →
   Domain-wide delegation → Add new.**
10. **Client ID**: the Unique ID from step 8.
11. **OAuth scopes** (comma-separated, exactly these):
    ```
    https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/spreadsheets
    ```
    Add `https://www.googleapis.com/auth/cloud-platform` too if you want Google
    text-to-speech.
12. Authorise. It can take a few minutes to propagate — up to 24 hours in the
    worst case.

Fill in:

```bash
GOOGLE_SERVICE_ACCOUNT_FILE=./secrets/service-account.json
GOOGLE_IMPERSONATED_USER=interviewer@yourdomain.com
INTERVIEWER_EMAIL=interviewer@yourdomain.com
INTERVIEW_TIMEZONE=Asia/Manila
```

Verify: start the app and check `GET /health/integrations` — `google_calendar`
should report `live: true`. If it says "manual Meet links", the path or the
delegation is wrong. Then book a test interview (step 4) and confirm the event
appears on the interviewer's calendar **with a Meet link**.

> **If delegation is blocked or slow**, you are not stuck: leave
> `GOOGLE_SERVICE_ACCOUNT_FILE` unset, create the Meet events by hand, and pass
> `"meeting_url"` in the booking request. Everything downstream works the same.

---

## Step 2 — A public HTTPS URL

Recall.ai must be able to reach this service. It calls back with live transcript
data during the call and lifecycle events after it.

**For development** — a *static* ngrok domain (the free tier includes one):

```bash
ngrok http 8000 --domain=your-name.ngrok-free.app
```

Use a static domain, not a random one. Every scheduled bot stores the callback
URL it was created with; if the URL changes, already-scheduled interviews break
silently.

**For production** — deploy the container anywhere that gives you HTTPS:

```bash
docker build -t ai-interviewer .
docker run -p 8000:8000 --env-file .env ai-interviewer
```

Railway, Render and Fly.io all work with the included `Dockerfile`. Set
`PUBLIC_BASE_URL` to the deployed URL, with no trailing slash.

**Run exactly one worker.** The `Dockerfile` already does. Live-call timing state
is in-process; two workers would split it mid-interview.

---

## Step 3 — Recall.ai: the bot

1. Sign up at <https://recall.ai>. Pick your region and stay on it — API keys,
   dashboards and hostnames are all per-region. `us-west-2` is the
   pay-as-you-go region.
2. **Developers → API Keys** → create a key → `RECALL_API_KEY`.
3. On the same page, create the **workspace verification secret** (starts with
   `whsec_`) → `RECALL_WEBHOOK_SECRET`. Without it, inbound webhooks arrive
   unsigned and this app refuses to trust them when `ENV=prod`.
4. **Webhooks dashboard → Add endpoint:**
   - URL: `https://your-domain/api/webhooks/recall/status`
   - Subscribe to: `bot.in_call_recording`, `bot.done`, `bot.call_ended`,
     `bot.fatal`. (Subscribing to all bot events is fine; the rest are ignored.)
5. Generate a real-time token: `openssl rand -hex 32` → `RECALL_REALTIME_TOKEN`.
   This is appended to the per-bot callback URL the app registers automatically;
   you do not configure it in the dashboard.

```bash
RECALL_API_KEY=...
RECALL_REGION=us-west-2
RECALL_WEBHOOK_SECRET=whsec_...
RECALL_REALTIME_TOKEN=...
```

You do **not** need Recall's Calendar V2 integration. This app creates the
calendar event itself and schedules the bot directly against the Meet URL, which
is simpler and gives you control over timing.

---

## Step 4 — Claude and the voice, then your first real interview

### Claude

<https://console.anthropic.com/settings/keys> → create a key.

```bash
ANTHROPIC_API_KEY=sk-ant-...
INTERVIEW_MODEL=claude-opus-5
SCORING_MODEL=claude-opus-5
INTERVIEW_EFFORT=low     # per-turn: latency matters
SCORING_EFFORT=high      # the scorecard is worth thinking about
```

Set a monthly spend limit in the console before you go live.

### Voice

ElevenLabs gives the most natural result. <https://elevenlabs.io> → Profile →
API key; pick a voice and copy its ID from the Voice Library.

```bash
TTS_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM
```

Cheaper alternative — OpenAI TTS:

```bash
TTS_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

Leave `TTS_PROVIDER=silent` and the bot joins, listens and scores but says
nothing audible. Useful for a first dry run.

### Your first real interview

```bash
# 1. Book yourself, 15+ minutes out (so you get a *scheduled* bot).
curl -X POST https://your-domain/api/bookings \
  -H "X-API-Key: $INTERNAL_API_KEY" -H 'Content-Type: application/json' \
  -d '{"full_name":"Your Name","email":"you@yourdomain.com",
       "scheduled_at":"2026-09-01T14:00:00+08:00","role":"virtual_assistant"}'

# 2. Join the Meet link from the calendar invite at the scheduled time.
# 3. The bot joins, greets you, and interviews you. Answer out loud.
# 4. Leave the call, wait ~30 s, then:
curl -H "X-API-Key: $INTERNAL_API_KEY" \
  https://your-domain/api/interviews/<id> | python -m json.tool
```

**Admit the bot from the waiting room.** Google Meet puts external participants
in a waiting room; someone in the call has to let the bot in. Either be in the
call yourself, or have the interviewer account host and admit it.

If the bot joins but never speaks: check `TTS_PROVIDER`, and check the logs for
`output_audio` errors. If it speaks but ignores you: the real-time webhook is
not arriving — verify `PUBLIC_BASE_URL` is exactly right, including scheme.

---

## Step 5 — Google Sheets (optional)

1. Create a spreadsheet. Rename the first tab to `Interviews`.
2. **Share it with the service-account email** (the `client_email` in your JSON
   key, ending `@…iam.gserviceaccount.com`) as an **Editor**. Forgetting this is
   the most common cause of silent write failures.
3. Copy the id from the URL: `docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`.

```bash
SHEETS_SPREADSHEET_ID=...
SHEETS_TAB_NAME=Interviews
```

The header row is written automatically on first use. Rows are keyed on
Interview ID, so re-running the pipeline updates the row instead of duplicating.

---

## Step 6 — Notifications (optional)

```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...   # Slack → Incoming Webhooks
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=interviewer@yourdomain.com
SMTP_PASSWORD=<a Google App Password, not the account password>
SMTP_FROM=interviewer@yourdomain.com
HR_NOTIFY_EMAILS=hr@yourdomain.com,you@yourdomain.com
```

Every completed interview posts to Slack. Only `REVIEW` results send email — the
point is to interrupt a human exactly when a human decision is needed.

---

## Step 7 — monday.com

**Your board is already identified.** I read your workspace: the live pipeline is
**Hiring / Talent Acquisition** in the *Twin Home Buyer* workspace.
(`Recruitment`, board `5586564350` in *Matrix Group One*, is a stale duplicate —
don't point at that one.)

```bash
MONDAY_API_KEY=<get this yourself: avatar -> Developers -> My Access Tokens>
MONDAY_BOARD_ID=4277249290

# Proposed group mapping — confirm this matches how you want candidates to flow:
MONDAY_GROUP_PROCEED=new_group59160      # "Initial Interview"  (advance to a human)
MONDAY_GROUP_REVIEW=new_group49772       # "Applicant Screening" (a human looks)
MONDAY_GROUP_REJECTED=new_group56420     # "Applicants that will not continue"

# Only columns that exist on your board:
MONDAY_COLUMN_MAP={"status":"dup__of_progress","score":"text8","interview_date":"date"}
```

Three things to know about that board specifically:

1. **`Score` is a text column** (`text8`), not a Numbers column. The client reads
   your board's schema and shapes each value to the actual column type, so this
   works — but you will not be able to sort or average on it. Add a Numbers
   column and remap `score` to it if you want that.
2. **The status labels don't match.** Your Status column has *Passed / Failed /
   Pending / final Screening / …*, not `PROCEED / REVIEW / DO_NOT_PROCEED`.
   As written the app creates those three as new labels. If you'd rather it
   reuse *Passed / Pending / Failed*, say so and I'll add a label mapping.
3. **There's no Email column**, so candidates are matched by nothing and a
   re-run creates a second item. Add an Email column and map it
   (`"email":"<new id>"`) to get update-in-place instead of duplicates.

The full scorecard — every category score with its justification, the summary,
strengths, concerns and the transcript link — is posted as an **item update**,
which needs no columns at all. So even with the minimal map above you lose
nothing; the columns are only for sorting and board views.

### Doing this for a different board

Get the group and column ids for any board with:

```bash
curl -s https://api.monday.com/v2 \
  -H "Authorization: $MONDAY_API_KEY" -H 'Content-Type: application/json' \
  -H 'API-Version: 2024-10' \
  -d '{"query":"{ boards(ids: 1234567890) { groups { id title } columns { id title type } } }"}' \
  | python -m json.tool
```

Recognised logical keys for `MONDAY_COLUMN_MAP`: `email`, `phone`, `status`,
`score`, `role`, `interview_date`, `summary`, `transcript`, `recording`,
`communication`, `experience`, `availability`. **Only what you map gets
written** — an unmapped field, a column id that doesn't exist, or a column of a
type the app can't safely write (file, people, formula) is skipped with a
warning rather than guessed at.

## Step 8 — Go-live checklist

Configuration:

- [ ] `ENV=prod`
- [ ] `INTERNAL_API_KEY` is a real random secret (`openssl rand -hex 32`), not the default
- [ ] `RECALL_WEBHOOK_SECRET` set — the app rejects unverified webhooks in prod
- [ ] `RECALL_REALTIME_TOKEN` is random, not `change-me`
- [ ] `PUBLIC_BASE_URL` matches the deployed HTTPS URL exactly, no trailing slash
- [ ] `GET /health/integrations` shows `live: true` for everything you intend to use
- [ ] `secrets/` is not committed (`git status --ignored`)
- [ ] Spend limits set in the Anthropic console and on Recall.ai
- [ ] Running one worker

Content — do this properly, it is the actual product:

- [ ] `questions.yaml` rewritten for your role, in your voice
- [ ] `rubric.yaml` weights and thresholds reflect what you actually care about
- [ ] `policies.yaml` disqualification thresholds are your real numbers (the
      defaults — 15 Mbps, 30 h/week, USD 1,500/month — are placeholders)
- [ ] Opening script discloses the AI and the recording (keep this)
- [ ] FAQ answers are accurate; nothing promises anything you cannot deliver

Process:

- [ ] Someone is available to admit the bot from the Meet waiting room
- [ ] A human reviews `REVIEW` results — and spot-checks `DO_NOT_PROCEED` ones
- [ ] No automated rejection email fires without a human looking first
- [ ] You have read the legal notes at the end of the README (recording consent,
      automated-decision notice, retention) and confirmed your obligations
- [ ] Retention policy decided and a deletion job scheduled — nothing here
      deletes transcripts on its own

Then run 3–5 interviews with people you know before pointing it at real
candidates. Read every transcript. Fix the questions that produced vague
answers. That iteration is where the quality comes from.

---

## Connecting a booking form

The service exposes `POST /api/bookings`; anything that can send a webhook can
drive it.

- **Calendly / Cal.com** — webhook on "invitee created" → map `name`, `email`,
  `start_time` to `full_name`, `email`, `scheduled_at`. Note that Calendly
  creates its own event; either pass its Meet link as `meeting_url`, or let this
  app create the event and use Calendly only for slot selection.
- **Google Forms** — Apps Script trigger on submit, `UrlFetchApp.fetch` to
  `/api/bookings` with the `X-API-Key` header.
- **Zapier / Make** — a Webhooks "POST" action; add `X-API-Key` as a header.

`scheduled_at` **must** include a timezone offset (`2026-09-01T14:00:00+08:00`).
A naive timestamp is rejected rather than guessed at, because guessing the
timezone means calling a candidate at the wrong hour.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Event created, no Meet link | Meet not enabled for the impersonated user, or delegation scopes wrong |
| `403` from Calendar | Delegation not authorised, or still propagating |
| Booking returns "Google Calendar is not configured" | Service-account path wrong; pass `meeting_url` meanwhile |
| Bot never joins | Check the bot in Recall's Explorer dashboard for a `fatal` sub-code |
| Bot joins, sits in waiting room | Nobody admitted it — Meet requires a host to let externals in |
| Bot joins but is silent | `TTS_PROVIDER=silent`, or TTS credentials failing (check logs) |
| Bot talks but ignores answers | Real-time webhook not arriving: verify `PUBLIC_BASE_URL`, and that the URL was correct *when the bot was created* |
| Bot interrupts constantly | Raise `ANSWER_DEBOUNCE_S` |
| Bot waits too long | Lower `ANSWER_DEBOUNCE_S` |
| `507` when booking | Ad-hoc bot pool exhausted — schedule more than 10 minutes ahead |
| Nothing in Sheets | Spreadsheet not shared with the service-account email |
| monday item created, columns empty | `MONDAY_COLUMN_MAP` ids wrong, or the columns are types the app won't write — the logs name each one it skipped |
| Duplicate monday items for one candidate | No `email` column mapped, so there is nothing to match on |
| Scores look meaningless | `ANTHROPIC_API_KEY` unset, so the scripted brain is running — check `/health/integrations` |
| Duplicate rows or items | Should not happen; both sinks are keyed. File it with the interview id |

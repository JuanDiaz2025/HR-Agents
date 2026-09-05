import fs from 'node:fs';
import { config, refreshConfig, ENV_FILE } from './config.js';

/**
 * Everything the app needs from the outside world, in one place: what each
 * connection is, where you get it, and - the part that matters - a live test
 * that proves it actually works before you try to place a call.
 *
 * Each test makes a real, cheap request to the real service. "Configured" is
 * not the same as "working", and the difference is usually discovered at the
 * worst possible moment.
 */

const E164 = /^\+[1-9]\d{7,14}$/;

export const FIELDS = [
  {
    key: 'TWILIO_ACCOUNT_SID', group: 'twilio', label: 'Twilio Account SID', secret: false,
    placeholder: 'AC0000000000000000000000000000000000',
    where: 'twilio.com/console - top of the dashboard, starts with AC',
  },
  {
    key: 'TWILIO_AUTH_TOKEN', group: 'twilio', label: 'Twilio Auth Token', secret: true,
    where: 'twilio.com/console - next to the SID, click to reveal',
  },
  {
    key: 'TWILIO_FROM_NUMBER', group: 'twilio', label: 'Twilio phone number', secret: false,
    placeholder: '+14155550100',
    where: 'Twilio Console - Phone Numbers - Manage - Active numbers. Must be voice-capable. This is the number the AI seller calls FROM.',
  },
  {
    key: 'SALES_LINE_NUMBER', group: 'line', label: 'Your sales line', secret: false,
    placeholder: '+14155550134',
    where: 'The number your reps answer. This is what gets called. Not a Twilio number - your real line.',
  },
  {
    key: 'OPENAI_API_KEY', group: 'openai', label: 'OpenAI API key', secret: true,
    placeholder: 'sk-...',
    where: 'platform.openai.com/api-keys. Needs Realtime API access - that is gated separately from a normal key.',
  },
  {
    key: 'ANTHROPIC_API_KEY', group: 'anthropic', label: 'Anthropic API key (optional)', secret: true,
    placeholder: 'sk-ant-...',
    where: 'console.anthropic.com - Settings - API keys. Optional: OpenAI can do the scoring instead.',
  },
  {
    key: 'PUBLIC_URL', group: 'tunnel', label: 'Public URL', secret: false,
    placeholder: 'https://something.ngrok-free.app',
    where: 'Run `ngrok http 3000` and paste the https URL. Twilio calls this back when the phone is answered - without it there is no audio.',
  },
  {
    key: 'DASHBOARD_TOKEN', group: 'security', label: 'Dashboard password', secret: true,
    where: 'Make one up. Required once PUBLIC_URL is set, so a stranger with the link cannot spend your call budget.',
  },
];

const mask = (value) => !value ? '' : value.length <= 8 ? '••••' : `${value.slice(0, 4)}${'•'.repeat(6)}${value.slice(-4)}`;

/** Never send real secrets back to the browser - only whether they are set. */
export function currentConnections() {
  return FIELDS.map((f) => {
    const value = process.env[f.key] || '';
    return {
      ...f,
      set: Boolean(value),
      display: f.secret ? mask(value) : value,
    };
  });
}

async function timedFetch(url, options = {}, ms = 12000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

const ok = (detail) => ({ status: 'ok', detail });
const warn = (detail, fix) => ({ status: 'warning', detail, fix });
const fail = (detail, fix) => ({ status: 'error', detail, fix });
const skip = (detail) => ({ status: 'not_set', detail });

async function testTwilio() {
  const { accountSid, authToken, fromNumber } = config.twilio;
  if (!accountSid || !authToken) return skip('Account SID and auth token not set yet.');

  const auth = 'Basic ' + Buffer.from(`${accountSid}:${authToken}`).toString('base64');
  const res = await timedFetch(`https://api.twilio.com/2010-04-01/Accounts/${accountSid}.json`, { headers: { Authorization: auth } });

  if (res.status === 401) return fail('Twilio rejected the credentials.', 'Check the SID and auth token - the token is the one you click to reveal, not the API key SID.');
  if (!res.ok) return fail(`Twilio returned ${res.status}.`);

  const account = await res.json();
  if (account.status === 'suspended') return fail('The Twilio account is suspended.', 'Check billing at twilio.com/console.');

  if (!fromNumber) return warn(`Connected to "${account.friendly_name}", but no Twilio phone number is set.`, 'Add TWILIO_FROM_NUMBER.');
  if (!E164.test(fromNumber)) return fail(`"${fromNumber}" is not a valid number.`, 'Use the +1XXXXXXXXXX format.');

  const numbers = await timedFetch(
    `https://api.twilio.com/2010-04-01/Accounts/${accountSid}/IncomingPhoneNumbers.json?PhoneNumber=${encodeURIComponent(fromNumber)}`,
    { headers: { Authorization: auth } },
  );
  if (!numbers.ok) return warn(`Connected to "${account.friendly_name}" but could not verify the phone number.`);

  const list = (await numbers.json()).incoming_phone_numbers || [];
  const match = list.find((n) => n.phone_number === fromNumber);
  if (!match) return fail(`${fromNumber} is not on this Twilio account.`, 'Use a number from Phone Numbers - Manage - Active numbers.');
  if (!match.capabilities?.voice) return fail(`${fromNumber} cannot make voice calls.`, 'Buy a voice-capable number.');

  const trial = account.type === 'Trial';
  return trial
    ? warn(`Connected to "${account.friendly_name}" (TRIAL account). ${fromNumber} is voice-ready.`,
        'Trial accounts can only call verified numbers. Verify your sales line in the Twilio console, or upgrade.')
    : ok(`Connected to "${account.friendly_name}". ${fromNumber} is voice-ready.`);
}

async function testOpenai() {
  if (!config.openai.apiKey) return skip('Not set. Needed for the AI seller\'s voice and for transcription.');

  const res = await timedFetch('https://api.openai.com/v1/models', { headers: { Authorization: `Bearer ${config.openai.apiKey}` } });
  if (res.status === 401) return fail('OpenAI rejected the key.', 'Copy it again from platform.openai.com/api-keys.');
  if (res.status === 429) return fail('OpenAI says the account is out of quota.', 'Add billing at platform.openai.com/account/billing.');
  if (!res.ok) return fail(`OpenAI returned ${res.status}.`);

  const ids = ((await res.json()).data || []).map((m) => m.id);
  const hasRealtime = ids.some((id) => id.includes('realtime'));
  const hasConfigured = ids.includes(config.openai.realtimeModel);

  if (!hasRealtime) {
    return fail('The key works, but this account has no Realtime model - the AI seller cannot talk.',
      'Realtime access is granted separately. Check platform.openai.com/settings/organization/limits, and that billing is active.');
  }
  if (!hasConfigured) {
    const available = ids.filter((id) => id.includes('realtime')).slice(0, 3).join(', ');
    return warn(`Realtime is available, but not "${config.openai.realtimeModel}".`, `Set OPENAI_REALTIME_MODEL to one of: ${available}`);
  }
  return ok(`Key works. Realtime model "${config.openai.realtimeModel}" is available, and transcription is ready.`);
}

async function testAnthropic() {
  if (!config.anthropic.apiKey) {
    return config.openai.apiKey
      ? skip('Not set - OpenAI will do the scoring instead. That is fine.')
      : skip('Not set. Without this or an OpenAI key, scoring falls back to keyword heuristics.');
  }
  const res = await timedFetch('https://api.anthropic.com/v1/models', {
    headers: { 'x-api-key': config.anthropic.apiKey, 'anthropic-version': '2023-06-01' },
  });
  if (res.status === 401) return fail('Anthropic rejected the key.', 'Copy it again from console.anthropic.com.');
  if (!res.ok) return fail(`Anthropic returned ${res.status}.`);
  return ok(`Key works. Scoring will use ${config.anthropic.scoringModel}.`);
}

/**
 * The one test people skip and then lose an hour to. It calls our own /health
 * endpoint through the public URL, so a pass means Twilio can genuinely reach
 * this exact process - not just that the URL is spelled correctly.
 */
async function testPublicUrl() {
  if (!config.publicUrl) return skip('Not set. Twilio needs a public https URL or the call connects to silence.');
  if (!config.publicUrl.startsWith('https://')) return fail('Must start with https:// - Twilio will not call an http URL.');

  try {
    const res = await timedFetch(`${config.publicUrl}/health`);
    if (!res.ok) return fail(`${config.publicUrl}/health returned ${res.status}.`, 'Is the tunnel pointing at the port this server is running on?');
    const body = await res.json();
    if (!body.ok) return fail('That URL answered, but it is not this app.', 'Point the tunnel at this server.');
    return ok('Twilio can reach this server. Round trip confirmed.');
  } catch (err) {
    return fail(`Could not reach ${config.publicUrl} (${err.name === 'AbortError' ? 'timed out' : err.message}).`,
      'Is ngrok still running? The URL changes every time it restarts.');
  }
}

function testSalesLine() {
  if (!config.salesLine) return skip('Not set. This is the number the AI seller dials.');
  if (!E164.test(config.salesLine)) return fail(`"${config.salesLine}" is not a valid number.`, 'Use the +1XXXXXXXXXX format.');
  return ok(`${config.salesLine} will be called. Make sure someone is there to answer it.`);
}

function testSecurity() {
  if (!config.publicUrl) return ok('Running locally, so no password is needed yet.');
  if (!config.dashboardToken) {
    return warn('The dashboard is on a public URL with no password.',
      'Anyone with the link can read your scorecards, and starting calls from the web UI is disabled until you set one.');
  }
  return ok('Dashboard is password protected.');
}

export async function testAll() {
  const [twilio, openai, anthropic, tunnel] = await Promise.all([
    testTwilio().catch((e) => fail(e.message)),
    testOpenai().catch((e) => fail(e.message)),
    testAnthropic().catch((e) => fail(e.message)),
    testPublicUrl().catch((e) => fail(e.message)),
  ]);

  const results = {
    twilio: { name: 'Twilio', purpose: 'Places the call and records it', ...twilio },
    openai: { name: 'OpenAI', purpose: "The AI seller's voice, and transcription", ...openai },
    line: { name: 'Your sales line', purpose: 'The number that rings', ...testSalesLine() },
    tunnel: { name: 'Public URL', purpose: 'How Twilio reaches this app', ...tunnel },
    anthropic: { name: 'Anthropic', purpose: 'Scoring and coaching (optional)', ...anthropic },
    security: { name: 'Dashboard password', purpose: 'Keeps strangers off your call budget', ...testSecurity() },
  };

  // A call needs all four of these actually working - not merely filled in.
  const required = ['twilio', 'openai', 'line', 'tunnel'];
  const blocking = required.filter((k) => results[k].status === 'error' || results[k].status === 'not_set');

  return {
    tested_at: new Date().toISOString(),
    results,
    ready_to_call: blocking.length === 0,
    blocking: blocking.map((k) => results[k].name),
  };
}

/**
 * Update .env in place, keeping comments and any keys we did not touch.
 * Blank submitted values leave the existing value alone, so a masked secret in
 * the form never silently wipes a working credential.
 */
export function saveConnections(updates) {
  const applied = [];
  const known = new Set(FIELDS.map((f) => f.key));

  const lines = fs.existsSync(ENV_FILE) ? fs.readFileSync(ENV_FILE, 'utf8').split('\n') : [];
  const pending = new Map();

  for (const [key, raw] of Object.entries(updates)) {
    if (!known.has(key)) continue;
    const value = String(raw ?? '').trim();
    if (!value) continue;
    if (/[\n\r]/.test(value)) throw new Error(`${key} cannot contain a line break`);
    pending.set(key, value);
  }

  const next = lines.map((line) => {
    const match = line.match(/^([A-Z_]+)=/);
    if (match && pending.has(match[1])) {
      const key = match[1];
      const value = pending.get(key);
      pending.delete(key);
      applied.push(key);
      return `${key}=${value}`;
    }
    return line;
  });

  for (const [key, value] of pending) {
    next.push(`${key}=${value}`);
    applied.push(key);
  }

  const body = next.join('\n').replace(/\n+$/, '') + '\n';
  fs.writeFileSync(ENV_FILE, body, { mode: 0o600 });

  // Apply immediately so the next test reflects what was just saved.
  for (const key of applied) process.env[key] = pending.get(key) ?? updates[key];
  for (const [key, value] of Object.entries(updates)) {
    if (known.has(key) && String(value ?? '').trim()) process.env[key] = String(value).trim();
  }
  refreshConfig();

  return { saved: applied, file: ENV_FILE };
}

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/** Minimal .env loader so we don't take a dependency for four lines of parsing. */
function loadDotEnv() {
  const file = path.join(ROOT, '.env');
  if (!fs.existsSync(file)) return;
  for (const raw of fs.readFileSync(file, 'utf8').split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (process.env[key] === undefined) process.env[key] = value;
  }
}
loadDotEnv();

export const config = {
  port: Number(process.env.PORT || 3000),
  publicUrl: (process.env.PUBLIC_URL || '').replace(/\/$/, ''),

  openai: {
    apiKey: process.env.OPENAI_API_KEY || '',
    realtimeModel: process.env.OPENAI_REALTIME_MODEL || 'gpt-4o-realtime-preview-2024-12-17',
    transcribeModel: process.env.OPENAI_TRANSCRIBE_MODEL || 'whisper-1',
    scoringModel: process.env.OPENAI_SCORING_MODEL || 'gpt-4o',
  },

  anthropic: {
    apiKey: process.env.ANTHROPIC_API_KEY || '',
    scoringModel: process.env.ANTHROPIC_SCORING_MODEL || 'claude-sonnet-5',
  },

  /** Which provider grades transcripts. 'auto' prefers Anthropic, falls back to OpenAI, then to the offline scorer. */
  scoringProvider: process.env.SCORING_PROVIDER || 'auto',

  twilio: {
    accountSid: process.env.TWILIO_ACCOUNT_SID || '',
    authToken: process.env.TWILIO_AUTH_TOKEN || '',
    fromNumber: process.env.TWILIO_FROM_NUMBER || '',
  },

  /** The sales line the AI seller dials - i.e. the number your reps answer. */
  salesLine: process.env.SALES_LINE_NUMBER || '',

  /** Optional shared password for the dashboard. Required to launch calls once PUBLIC_URL is set. */
  dashboardToken: process.env.DASHBOARD_TOKEN || '',

  dataDir: process.env.DATA_DIR || path.join(ROOT, 'data'),
  personaDir: path.join(ROOT, 'config', 'personas'),
  rubricPath: process.env.RUBRIC_PATH || path.join(ROOT, 'config', 'scoring-rubric.json'),
};

export function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

export function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n');
  return file;
}

/** Reports which capabilities are actually wired up, so `doctor` and the CLI can degrade honestly. */
export function capabilities() {
  return {
    live_calls: Boolean(
      config.twilio.accountSid && config.twilio.authToken && config.twilio.fromNumber &&
      config.openai.apiKey && config.publicUrl && config.salesLine
    ),
    transcription: Boolean(config.openai.apiKey),
    llm_scoring: Boolean(config.anthropic.apiKey || config.openai.apiKey),
    offline_scoring: true,
    mining: true,
  };
}

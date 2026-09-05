import fs from 'node:fs';
import path from 'node:path';
import { config, readJson, writeJson } from './config.js';
import { transcribeFile } from './transcribe.js';
import { complete, extractJson, activeProvider } from './llm.js';
import { loadPersona, savePersona } from './persona.js';

const AUDIO = /\.(wav|mp3|m4a|mp4|mpga|webm|ogg|flac)$/i;

/**
 * Phase 2 of the day-one plan: turn real recorded calls into the raw material
 * for the AI seller and the coaching standard.
 *
 * Outcome labels come from either a manifest.json in the same folder
 * ({"file.wav": "win"}) or a win-/loss- filename prefix. Unlabeled calls are
 * still transcribed and mined, they just do not feed the win/loss comparison.
 */
export function discoverCalls(dir) {
  if (!fs.existsSync(dir)) throw new Error(`No such directory: ${dir}`);
  const manifestPath = path.join(dir, 'manifest.json');
  const manifest = fs.existsSync(manifestPath) ? readJson(manifestPath) : {};

  return fs.readdirSync(dir)
    .filter((f) => AUDIO.test(f))
    .map((file) => {
      const lower = file.toLowerCase();
      const outcome = manifest[file]
        || (lower.startsWith('win') ? 'win' : lower.startsWith('loss') || lower.startsWith('lose') ? 'loss' : 'unknown');
      return { file, outcome, fullPath: path.join(dir, file) };
    });
}

const MINER_SYSTEM = `You analyze recorded acquisitions calls for a residential real-estate investor.
You extract only what is actually present in the transcripts. You never invent an objection, a phrase, or a statistic.
Return only JSON.`;

function minerPrompt(calls) {
  const body = calls.map((c, i) => `--- CALL ${i + 1} (outcome: ${c.outcome}) ---\n${c.text}`).join('\n\n').slice(0, 400000);
  return `Here are ${calls.length} real transcribed calls between our reps and property sellers.

${body}

Return JSON:
{
  "seller_objections": [{"objection": "in the seller's own words", "frequency": <how many calls it appeared in>, "best_response_heard": "quote from a rep, or null"}],
  "seller_language": ["distinctive phrases sellers actually used - short, verbatim"],
  "seller_motivations": [{"motivation": "...", "how_it_surfaced": "the question that surfaced it"}],
  "questions_that_worked": ["rep questions that opened the seller up, verbatim"],
  "win_vs_loss": {"what_winners_did": ["..."], "what_losers_did": ["..."]},
  "scenario_seeds": [{"headline": "a realistic seller scenario drawn from these calls", "property": "...", "situation": "...", "hardest_moment": "..."}],
  "rubric_gaps": ["behaviors that clearly mattered in these calls but are not in our scoring rubric"]
}
Order seller_objections by frequency, most common first. Cap each list at 12 items.`;
}

/** Cheap, deterministic pass so mining still produces something without an LLM key. */
export function statisticalInsights(calls) {
  const counts = new Map();
  const phrase = /\b(zillow|not in a rush|talk to my|lowball|thinking about it|what('| i)s your offer|realtor|agent|as-is|probate|inherited|tenant|behind on|foreclos\w+)\b/gi;
  for (const call of calls) {
    for (const match of call.text.matchAll(phrase)) {
      const key = match[0].toLowerCase();
      counts.set(key, (counts.get(key) || 0) + 1);
    }
  }
  return {
    call_count: calls.length,
    outcomes: calls.reduce((acc, c) => ({ ...acc, [c.outcome]: (acc[c.outcome] || 0) + 1 }), {}),
    common_terms: [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([term, n]) => ({ term, mentions: n })),
    average_words_per_call: Math.round(calls.reduce((n, c) => n + c.text.split(/\s+/).length, 0) / (calls.length || 1)),
  };
}

export async function mineCalls(dir, { limit = 25 } = {}) {
  const found = discoverCalls(dir).slice(0, limit);
  if (!found.length) throw new Error(`No audio files found in ${dir}`);

  const cacheDir = path.join(config.dataDir, 'mined');
  fs.mkdirSync(cacheDir, { recursive: true });

  const calls = [];
  for (const item of found) {
    const cache = path.join(cacheDir, item.file.replace(AUDIO, '') + '.json');
    if (fs.existsSync(cache)) {
      calls.push(readJson(cache));
      console.log(`  cached   ${item.file}`);
      continue;
    }
    console.log(`  transcribing ${item.file} ...`);
    const result = await transcribeFile(item.fullPath, {
      channelSpeakers: ['rep', 'seller'],
      prompt: 'A phone call between a real estate acquisitions representative and a property seller.',
    });
    const record = { file: item.file, outcome: item.outcome, turns: result.turns, text: result.text, diarized: result.diarized };
    writeJson(cache, record);
    calls.push(record);
  }

  const insights = { generated_at: new Date().toISOString(), source_dir: path.resolve(dir), stats: statisticalInsights(calls) };

  if (activeProvider()) {
    const raw = await complete({ system: MINER_SYSTEM, prompt: minerPrompt(calls), maxTokens: 6000, temperature: 0.2 });
    Object.assign(insights, extractJson(raw));
    insights.analyzed_by = `llm:${activeProvider()}`;
  } else {
    insights.analyzed_by = 'statistical-only';
    insights.note = 'No LLM credentials configured, so only the statistical pass ran. Set ANTHROPIC_API_KEY or OPENAI_API_KEY for the full extraction.';
  }

  const out = writeJson(path.join(config.dataDir, 'insights.json'), insights);
  return { insights, file: out, callCount: calls.length };
}

/**
 * Fold mined insights into a persona so the AI seller talks like the sellers we
 * actually get on the phone, instead of like a language model imagining one.
 */
export function applyInsightsToPersona(insights, personaId = 'larry') {
  const persona = loadPersona(personaId);
  const before = { objections: persona.objections.length, traits: (persona.personality.traits || []).length };

  const existing = new Set(persona.objections.map((o) => o.line.toLowerCase()));
  for (const item of (insights.seller_objections || []).slice(0, 8)) {
    if (!item.objection || existing.has(item.objection.toLowerCase())) continue;
    persona.objections.push({
      trigger: `a natural moment for this objection (heard in ${item.frequency || 1} real call(s))`,
      line: item.objection,
      source: 'mined',
    });
  }

  persona.observed_language = (insights.seller_language || []).slice(0, 15);
  persona.observed_motivations = (insights.seller_motivations || []).map((m) => m.motivation).slice(0, 10);
  persona.version = (persona.version || 1) + 1;
  persona.updated_at = new Date().toISOString();
  persona.updated_from = insights.source_dir || 'insights.json';

  savePersona(persona);
  return { persona, added_objections: persona.objections.length - before.objections };
}

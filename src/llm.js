import { config } from './config.js';

/**
 * One text-completion entry point over whichever provider has a key.
 * Scoring and call mining are plain text-in / JSON-out jobs, so they do not
 * need a provider SDK - and keeping it provider-agnostic means the grader can
 * move without touching the rubric or the report.
 */
export function activeProvider() {
  const wanted = config.scoringProvider;
  if (wanted === 'anthropic') return config.anthropic.apiKey ? 'anthropic' : null;
  if (wanted === 'openai') return config.openai.apiKey ? 'openai' : null;
  if (wanted === 'offline') return null;
  if (config.anthropic.apiKey) return 'anthropic';
  if (config.openai.apiKey) return 'openai';
  return null;
}

export async function complete({ system, prompt, maxTokens = 4000, temperature = 0.2 }) {
  const provider = activeProvider();
  if (!provider) throw new Error('No LLM credentials configured (set ANTHROPIC_API_KEY or OPENAI_API_KEY)');
  return provider === 'anthropic'
    ? completeAnthropic({ system, prompt, maxTokens, temperature })
    : completeOpenai({ system, prompt, maxTokens, temperature });
}

async function completeAnthropic({ system, prompt, maxTokens, temperature }) {
  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'x-api-key': config.anthropic.apiKey,
      'anthropic-version': '2023-06-01',
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      model: config.anthropic.scoringModel,
      max_tokens: maxTokens,
      temperature,
      system,
      messages: [{ role: 'user', content: prompt }],
    }),
  });
  if (!res.ok) throw new Error(`Anthropic ${res.status}: ${(await res.text()).slice(0, 300)}`);
  const body = await res.json();
  return (body.content || []).filter((b) => b.type === 'text').map((b) => b.text).join('');
}

async function completeOpenai({ system, prompt, maxTokens, temperature }) {
  const res = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: { Authorization: `Bearer ${config.openai.apiKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: config.openai.scoringModel,
      max_tokens: maxTokens,
      temperature,
      messages: [{ role: 'system', content: system }, { role: 'user', content: prompt }],
    }),
  });
  if (!res.ok) throw new Error(`OpenAI ${res.status}: ${(await res.text()).slice(0, 300)}`);
  const body = await res.json();
  return body.choices?.[0]?.message?.content || '';
}

/** Pull the first JSON object out of a model response, tolerating code fences and preamble. */
export function extractJson(text) {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  const candidate = fenced ? fenced[1] : text;
  const start = candidate.indexOf('{');
  const end = candidate.lastIndexOf('}');
  if (start === -1 || end === -1) throw new Error(`Model did not return JSON: ${text.slice(0, 200)}`);
  return JSON.parse(candidate.slice(start, end + 1));
}

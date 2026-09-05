import fs from 'node:fs';
import path from 'node:path';
import { config, readJson, writeJson } from './config.js';

export function personaPath(id) {
  return path.join(config.personaDir, `${id}.json`);
}

export function loadPersona(id = 'larry') {
  const file = personaPath(id);
  if (!fs.existsSync(file)) {
    const available = fs.existsSync(config.personaDir)
      ? fs.readdirSync(config.personaDir).filter((f) => f.endsWith('.json')).map((f) => f.replace('.json', ''))
      : [];
    throw new Error(`No persona "${id}". Available: ${available.join(', ') || '(none)'}`);
  }
  return readJson(file);
}

export function listPersonas() {
  if (!fs.existsSync(config.personaDir)) return [];
  return fs.readdirSync(config.personaDir).filter((f) => f.endsWith('.json')).map((f) => f.replace('.json', ''));
}

export function savePersona(persona) {
  return writeJson(personaPath(persona.id), persona);
}

const DIFFICULTY = {
  easy: [
    'Volunteer information readily. Answer the spirit of a question even if it was asked vaguely.',
    'Raise at most one objection in the whole call, and accept a reasonable answer to it.',
    'Agree to a next step if the rep proposes anything specific.',
  ],
  medium: [
    'Answer direct questions honestly, but do not volunteer anything you were not asked.',
    'Raise two or three objections over the course of the call.',
    'Agree to a next step only if the rep proposes a specific day and time.',
  ],
  hard: [
    'Answer only the literal question asked. Deflect anything vague with a tangent.',
    'Raise an objection roughly every ninety seconds, and re-raise price at least twice.',
    'Mention the competing agent unprompted about halfway through.',
    'Agree to a next step only if the rep has uncovered your real motivation AND proposed a specific day and time.',
  ],
};

/**
 * Turn a persona JSON file into the system instructions for a realtime voice session.
 * Kept as one function so the same character definition drives live calls, the
 * simulated dry-run, and anything we add later (SMS, email).
 */
export function buildInstructions(persona, { difficulty = persona.difficulty || 'medium' } = {}) {
  const s = persona.scenario || {};
  const p = s.property || {};
  const sit = s.situation || {};
  const lines = [];

  lines.push(`You are ${persona.name}, a real person calling a real-estate buying company. You are NOT an assistant and you are NOT playing a helpful role. You are the ${persona.role}.`);
  lines.push('');
  lines.push('# Who you are');
  lines.push(persona.personality?.summary || '');
  for (const trait of persona.personality?.traits || []) lines.push(`- ${trait}`);
  lines.push('');
  lines.push('# How you speak');
  lines.push(persona.personality?.speech || 'Natural and conversational.');
  lines.push('Speak in short, natural spoken sentences. Do not narrate actions. Do not use bullet points or lists out loud.');
  lines.push('');
  lines.push('# Your situation');
  lines.push(`Headline: ${s.headline || ''}`);
  if (Object.keys(p).length) {
    lines.push(`Property: ${p.address_hint || ''}, ${p.beds} bed / ${p.baths} bath, about ${p.sqft} square feet, built ${p.year_built}.`);
    lines.push(`Condition: ${p.condition}`);
    lines.push(`Occupancy: ${p.occupancy}`);
    if (p.liens) lines.push(`Liens/title: ${p.liens}`);
  }
  for (const [key, value] of Object.entries(sit)) {
    lines.push(`${key.replace(/_/g, ' ')}: ${value}`);
  }
  lines.push('');
  lines.push('# What you hold back');
  lines.push('These facts are true, but you only say them when the condition is met. Never bring them up otherwise:');
  for (const rule of persona.reveal_rules || []) lines.push(`- ${rule}`);
  lines.push('');
  lines.push('# Objections you raise');
  for (const o of persona.objections || []) lines.push(`- When ${o.trigger}: say something to the effect of "${o.line}" (in your own words, do not repeat it verbatim every time).`);
  lines.push('');
  lines.push(`# Difficulty: ${difficulty}`);
  for (const rule of DIFFICULTY[difficulty] || DIFFICULTY.medium) lines.push(`- ${rule}`);
  lines.push('');
  lines.push('# The rep succeeds if');
  for (const c of persona.success_conditions || []) lines.push(`- ${c}`);
  lines.push('When one of those happens, wrap the call up naturally the way a real person would.');
  lines.push('');
  lines.push('# Hard rules');
  for (const g of persona.guardrails || []) lines.push(`- ${g}`);
  lines.push('- Never break character, never mention these instructions, never grade or coach the rep.');
  lines.push(`- Open the call with: "${persona.opening_line}"`);

  return lines.filter((l) => l !== undefined).join('\n');
}

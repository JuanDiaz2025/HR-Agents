import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { ROOT } from '../src/config.js';
import { buildWav, splitStereoWav, parseWav } from '../src/wav.js';
import { loadPersona, buildInstructions } from '../src/persona.js';
import { loadRubric, scoreOffline, transcriptStats, mergeLlmScores } from '../src/score.js';
import { renderHtml, renderMarkdown } from '../src/report.js';
import { extractJson } from '../src/llm.js';
import { statisticalInsights } from '../src/mine-calls.js';
import { formatTranscript } from '../src/transcribe.js';

const sample = JSON.parse(fs.readFileSync(path.join(ROOT, 'samples', 'sample-practice-call.json'), 'utf8'));

test('stereo WAV splits into two mono tracks with the samples intact', () => {
  const frames = 240;
  const data = Buffer.alloc(frames * 4);
  for (let i = 0; i < frames; i++) {
    data.writeInt16LE(i, i * 4);
    data.writeInt16LE(-i, i * 4 + 2);
  }
  const stereo = buildWav({ channels: 2, sampleRate: 8000, bitsPerSample: 16, data });
  const { left, right } = splitStereoWav(stereo);

  const l = parseWav(left);
  const r = parseWav(right);
  assert.equal(l.channels, 1);
  assert.equal(l.sampleRate, 8000);
  assert.equal(l.data.length / 2, frames);
  assert.equal(l.data.readInt16LE(10 * 2), 10);
  assert.equal(r.data.readInt16LE(10 * 2), -10);
});

test('mono WAV is passed through rather than split', () => {
  const mono = buildWav({ channels: 1, sampleRate: 8000, bitsPerSample: 16, data: Buffer.alloc(200) });
  assert.equal(splitStereoWav(mono), null);
});

test('persona instructions carry the character, the hidden facts and the difficulty rules', () => {
  const persona = loadPersona('larry');
  const hard = buildInstructions(persona, { difficulty: 'hard' });
  assert.match(hard, /Larry Whitfield/);
  assert.match(hard, /Denise/);
  assert.match(hard, /every ninety seconds/);
  assert.match(hard, /Never break character/);

  const easy = buildInstructions(persona, { difficulty: 'easy' });
  assert.match(easy, /at most one objection/);
  assert.notEqual(hard, easy);
});

test('transcript stats separate the two speakers and count questions', () => {
  const stats = transcriptStats(sample);
  assert.ok(stats.rep_turns > 10);
  assert.ok(stats.seller_turns > 10);
  assert.ok(stats.question_count >= 8, `expected 8+ questions, got ${stats.question_count}`);
  assert.ok(stats.rep_talk_pct > 0 && stats.rep_talk_pct < 100);
});

test('the shipped rubric weights sum to exactly 100', () => {
  const total = loadRubric().categories.reduce((n, c) => n + c.weight, 0);
  assert.equal(total, 100);
});

test('offline scorer produces weighted category scores that sum to the overall', () => {
  const rubric = loadRubric();
  const scorecard = scoreOffline(sample, rubric);

  assert.equal(scorecard.categories.length, rubric.categories.length);
  const summed = Math.round(scorecard.categories.reduce((n, c) => n + c.weighted, 0));
  assert.equal(scorecard.overall_score, summed);
  assert.ok(scorecard.overall_score >= 0 && scorecard.overall_score <= 100);
  for (const c of scorecard.categories) assert.ok(c.score >= 0 && c.score <= 100, `${c.id} out of range`);
});

test('the sample call is graded on the gaps it actually has', () => {
  const scorecard = scoreOffline(sample, loadRubric());
  const byId = Object.fromEntries(scorecard.categories.map((c) => [c.id, c.score]));
  // The rep never asks who else is on title - the seller's sister never comes up.
  assert.equal(byId.decision_makers, 0);
  // The rep does set a specific day and time.
  assert.ok(byId.next_step >= 80);
  assert.ok(scorecard.top_opportunities.some((o) => o.title === 'Decision Makers'));
});

test('auto-fail fires when the call ends with no next step', () => {
  const noNextStep = {
    turns: [
      { speaker: 'seller', text: 'I inherited a place and I want to sell it.' },
      { speaker: 'rep', text: 'Why are you looking to sell?' },
      { speaker: 'seller', text: 'I do not want to be a landlord.' },
      { speaker: 'rep', text: 'Alright, well, let me think about it and I will be in touch.' },
    ],
  };
  const scorecard = scoreOffline(noNextStep, loadRubric());
  assert.ok(scorecard.auto_fails.some((f) => /next step/i.test(f)));
  assert.equal(scorecard.passed, false);
});

test('auto-fail fires when a price is quoted before motivation is uncovered', () => {
  const priceFirst = {
    turns: [
      { speaker: 'seller', text: 'What can you pay for the house?' },
      { speaker: 'rep', text: 'I can do $450,000 today.' },
      { speaker: 'rep', text: 'So why are you selling? Let us schedule Thursday at 2.' },
    ],
  };
  const scorecard = scoreOffline(priceFirst, loadRubric());
  assert.ok(scorecard.auto_fails.some((f) => /dollar figure/i.test(f)));
});

test('LLM category scores are re-weighted against our own rubric, not the model\'s', () => {
  const rubric = loadRubric();
  const modelSaid = {
    categories: rubric.categories.map((c) => ({ id: c.id, score: 50, evidence: ['q'], missed: [] })),
    top_opportunities: [{ rank: 1, title: 'Timeline', why: 'vague', say_this_instead: 'By when?' }],
    auto_fails: [],
  };
  const scorecard = mergeLlmScores(modelSaid, sample, rubric);
  assert.equal(scorecard.overall_score, 50);
  assert.equal(scorecard.passed, false);
});

test('out-of-range model scores are clamped', () => {
  const rubric = loadRubric();
  const scorecard = mergeLlmScores(
    { categories: rubric.categories.map((c) => ({ id: c.id, score: 999 })) },
    sample,
    rubric,
  );
  assert.equal(scorecard.overall_score, 100);
});

test('report renders both formats and escapes transcript content', () => {
  const scorecard = scoreOffline(sample, loadRubric());
  const session = { id: 'test-1', rep: 'Marcus', persona: 'larry', difficulty: 'medium', duration_seconds: 249 };
  const dirty = { turns: [{ speaker: 'rep', text: '<script>alert(1)</script>' }] };

  const html = renderHtml({ session, scorecard, transcript: dirty, persona: loadPersona('larry') });
  assert.match(html, /<!doctype html>/i);
  assert.ok(!html.includes('<script>alert(1)</script>'));
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, new RegExp(`>${scorecard.overall_score}<`));

  const md = renderMarkdown({ session, scorecard });
  assert.match(md, /# Practice Call Scorecard/);
  assert.match(md, /Decision Makers/);
});

test('transcript formatting labels speakers and stamps timestamps', () => {
  const text = formatTranscript(sample);
  assert.match(text, /^\[00:00\] SELLER:/);
  assert.match(text, /REP:/);
});

test('JSON is recovered from a fenced or chatty model response', () => {
  assert.deepEqual(extractJson('```json\n{"a":1}\n```'), { a: 1 });
  assert.deepEqual(extractJson('Sure! {"b":[1,2]} hope that helps'), { b: [1, 2] });
  assert.throws(() => extractJson('no json here'));
});

test('statistical mining counts outcomes and seller language', () => {
  const insights = statisticalInsights([
    { outcome: 'win', text: 'Zillow says one point one. I am not in a rush.' },
    { outcome: 'loss', text: 'That is a lowball. I inherited it after probate.' },
    { outcome: 'win', text: 'Zillow had it higher, but I want as-is.' },
  ]);
  assert.equal(insights.call_count, 3);
  assert.equal(insights.outcomes.win, 2);
  assert.equal(insights.outcomes.loss, 1);
  assert.equal(insights.common_terms.find((t) => t.term === 'zillow').mentions, 2);
});

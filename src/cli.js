#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { config, capabilities } from './config.js';
import * as store from './store.js';
import { loadPersona, listPersonas, buildInstructions } from './persona.js';
import { transcribeAudio, transcribeFile, formatTranscript } from './transcribe.js';
import { scoreTranscript, scoreOffline, loadRubric } from './score.js';
import { renderReport } from './report.js';
import { mineCalls, applyInsightsToPersona } from './mine-calls.js';
import { complete, activeProvider } from './llm.js';
import { ROOT } from './config.js';

function parseFlags(argv) {
  const flags = {};
  const positional = [];
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith('--')) {
      const [key, inline] = argv[i].slice(2).split('=');
      if (inline !== undefined) flags[key] = inline;
      else if (argv[i + 1] && !argv[i + 1].startsWith('--')) flags[key] = argv[++i];
      else flags[key] = true;
    } else positional.push(argv[i]);
  }
  return { flags, positional };
}

const USAGE = `AI Sales Practice & Testing System

  npm run demo                      Run the whole loop on a sample call - no credentials needed
  npm run demo -- --seed 12         Fill the dashboard with sample calls to click through
  npm run doctor                    Show which phases are wired up and what is missing

  npm run mine -- <dir>             Phase 2: transcribe and analyze real recorded calls
    --apply-to <persona>              also fold the findings into a persona
  node src/cli.js persona <id>      Phase 3: print the AI seller's instructions
    --difficulty easy|medium|hard
  node src/cli.js simulate          Phase 3/4 dry run: practice by typing, no phone needed
    --persona larry --difficulty medium --rep "Marcus"

  npm run serve                     Phase 4: start the webhook + media-stream server
  npm run call -- --to +14155550134 Phase 4: have the AI seller call your sales line
    --persona larry --difficulty medium --rep "Marcus"

  npm run transcribe -- <session>   Phase 5: transcribe a session recording
  npm run score -- <session>        Phase 5: score against config/scoring-rubric.json
  npm run report -- <session>       Phase 5: write report.html and report.md
  node src/cli.js sessions          List every practice session
`;

async function cmdDoctor() {
  const caps = capabilities();
  console.log('Capability check\n');
  const rows = [
    ['Mine real calls (transcribe + analyze)', caps.transcription, 'OPENAI_API_KEY'],
    ['AI seller persona + instructions', true, '-'],
    ['Typed practice (simulate)', caps.llm_scoring, 'ANTHROPIC_API_KEY or OPENAI_API_KEY'],
    ['Live phone call (AI seller dials your line)', caps.live_calls, 'TWILIO_* + OPENAI_API_KEY + PUBLIC_URL + SALES_LINE_NUMBER'],
    ['Transcribe a recording', caps.transcription, 'OPENAI_API_KEY'],
    ['LLM scoring + coaching', caps.llm_scoring, 'ANTHROPIC_API_KEY or OPENAI_API_KEY'],
    ['Offline scoring + report', true, '-'],
  ];
  for (const [label, ok, needs] of rows) {
    console.log(`  ${ok ? '[ready]  ' : '[missing]'} ${label.padEnd(44)} ${ok ? '' : 'needs ' + needs}`);
  }
  console.log(`\nScoring provider: ${activeProvider() || 'none (offline heuristics only)'}`);
  console.log(`Personas: ${listPersonas().join(', ') || '(none)'}`);
  console.log(`Data dir: ${config.dataDir}`);
  if (!fs.existsSync(path.join(ROOT, '.env'))) console.log('\nNo .env yet. Copy .env.example to .env and fill in what you have.');
}

async function cmdDemo(flags = {}) {
  if (flags.seed) {
    const count = flags.seed === true ? 12 : Number(flags.seed);
    const { seedSessions } = await import('./seed.js');
    console.log(`Seeding ${count} sample practice calls so the dashboard has something to show...\n`);
    const created = await seedSessions(count);
    for (const c of created) console.log(`  ${c.rep.padEnd(8)} ${String(c.score).padStart(3)}/100  ${c.difficulty.padEnd(6)} (${c.improvements} coached behaviors)`);
    console.log(`\nSeeded ${created.length} sessions. Scores come from the real scorer on real transcripts.`);
    console.log(`Start the dashboard:  npm run serve   ->  http://localhost:3000`);
    console.log(`Clear the sample data: rm -rf data/`);
    return;
  }
  return cmdDemoSingle();
}

async function cmdDemoSingle() {
  console.log('Running the full loop on the sample call (no credentials required)\n');
  const sample = JSON.parse(fs.readFileSync(path.join(ROOT, 'samples', 'sample-practice-call.json'), 'utf8'));
  const session = store.createSession({ id: store.newSessionId('demo'), persona: 'larry', rep: 'Marcus (sample)', difficulty: 'medium', status: 'transcribed', duration_seconds: 249 });

  store.saveArtifact(session.id, 'transcript.json', { session_id: session.id, ...sample });
  store.saveArtifact(session.id, 'transcript.txt', formatTranscript(sample));
  console.log(`1. transcript  -> ${path.join(store.sessionDir(session.id), 'transcript.json')}`);

  const scorecard = await scoreTranscript(session.id);
  console.log(`2. scorecard   -> ${scorecard.overall_score}/100 (${scorecard.scorer}), ${scorecard.passed ? 'meets' : 'below'} the standard`);
  for (const c of scorecard.categories) console.log(`     ${String(c.score).padStart(3)}  ${c.name}`);

  const files = renderReport(session.id);
  store.updateSession(session.id, { status: 'scored', score: scorecard.overall_score });
  console.log(`3. report      -> ${files.html}`);
  console.log(`\nTop opportunities:`);
  for (const o of scorecard.top_opportunities) console.log(`   ${o.rank}. ${o.title} - ${o.why}`);
  console.log(`\nOpen the report:  open ${files.html}`);
}

async function cmdMine(positional, flags) {
  const dir = positional[0];
  if (!dir) return console.error('Usage: npm run mine -- <directory-of-recordings>');
  console.log(`Mining calls in ${dir}\n`);
  const { insights, file, callCount } = await mineCalls(dir, { limit: Number(flags.limit || 25) });
  console.log(`\nAnalyzed ${callCount} calls (${insights.analyzed_by}) -> ${file}`);
  console.log(`Outcomes: ${JSON.stringify(insights.stats.outcomes)}`);
  for (const o of (insights.seller_objections || []).slice(0, 6)) console.log(`  objection (x${o.frequency}): "${o.objection}"`);
  if (flags['apply-to']) {
    const { persona, added_objections } = applyInsightsToPersona(insights, flags['apply-to']);
    console.log(`\nUpdated persona "${persona.id}" to v${persona.version} (+${added_objections} objections from real calls)`);
  }
}

async function cmdPersona(positional, flags) {
  const persona = loadPersona(positional[0] || 'larry');
  console.log(buildInstructions(persona, { difficulty: flags.difficulty }));
}

/** Typed practice against the persona. Same character, same difficulty, no telephony. */
async function cmdSimulate(flags) {
  if (!activeProvider()) {
    console.error('simulate needs ANTHROPIC_API_KEY or OPENAI_API_KEY');
    process.exitCode = 1;
    return;
  }
  const persona = loadPersona(flags.persona || 'larry');
  const difficulty = flags.difficulty || persona.difficulty;
  const instructions = buildInstructions(persona, { difficulty });
  const session = store.createSession({ id: store.newSessionId('sim'), persona: persona.id, difficulty, rep: flags.rep || null, mode: 'simulated' });

  const turns = [{ speaker: 'seller', text: persona.opening_line }];
  console.log(`Practice call with ${persona.name} (${difficulty}). Type your side. "quit" ends the call.\n`);
  console.log(`SELLER: ${persona.opening_line}\n`);

  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const ask = (q) => new Promise((resolve) => rl.question(q, resolve));

  while (true) {
    const line = (await ask('REP: ')).trim();
    if (!line || ['quit', 'exit', 'bye'].includes(line.toLowerCase())) break;
    turns.push({ speaker: 'rep', text: line });

    const history = turns.map((t) => `${t.speaker === 'rep' ? 'REP' : 'YOU'}: ${t.text}`).join('\n');
    const reply = (await complete({
      system: instructions,
      prompt: `${history}\n\nReply as ${persona.name}, in one to three spoken sentences. Return only the words you say.`,
      maxTokens: 300,
      temperature: 0.9,
    })).trim();

    turns.push({ speaker: 'seller', text: reply });
    console.log(`\nSELLER: ${reply}\n`);
  }
  rl.close();

  const transcript = { session_id: session.id, source: 'simulated', diarized: true, turns };
  store.saveArtifact(session.id, 'transcript.json', transcript);
  store.saveArtifact(session.id, 'transcript.txt', formatTranscript(transcript));
  console.log('\nScoring...');
  const scorecard = await scoreTranscript(session.id, { persona });
  const files = renderReport(session.id);
  store.updateSession(session.id, { status: 'scored', score: scorecard.overall_score });
  console.log(`\n${scorecard.overall_score}/100 - ${files.html}`);
  for (const o of scorecard.top_opportunities || []) console.log(`  ${o.rank}. ${o.title}: ${o.why}`);
}

async function cmdCall(flags) {
  const caps = capabilities();
  if (!caps.live_calls) {
    console.error('Live calls are not wired up yet. Run `npm run doctor` to see what is missing.');
    process.exitCode = 1;
    return;
  }
  const { launchCall } = await import('./launch-call.js');
  const result = await launchCall({
    to: flags.to,
    persona: flags.persona || 'larry',
    difficulty: flags.difficulty,
    rep: flags.rep,
  });

  console.log(`Calling ${result.to} as ${result.persona} (${result.difficulty}).`);
  console.log(`  session: ${result.session_id}`);
  console.log(`  call:    ${result.call_sid} (${result.status})`);
  console.log(`\nAnswer the phone. When the call ends the server transcribes, scores and writes the report automatically.`);
  console.log(`Then: npm run report -- ${result.session_id}`);
}

async function main() {
  const [command, ...rest] = process.argv.slice(2);
  const { flags, positional } = parseFlags(rest);

  switch (command) {
    case 'demo': return cmdDemo(flags);
    case 'doctor': return cmdDoctor();
    case 'mine': return cmdMine(positional, flags);
    case 'persona': return cmdPersona(positional, flags);
    case 'simulate': return cmdSimulate(flags);
    case 'call': return cmdCall(flags);

    case 'serve': {
      const { startServer } = await import('./server.js');
      startServer(Number(flags.port || config.port));
      return;
    }

    case 'transcribe': {
      if (positional[0] && fs.existsSync(positional[0])) {
        const result = await transcribeFile(positional[0]);
        console.log(formatTranscript(result));
        return;
      }
      const transcript = await transcribeAudio(positional[0]);
      console.log(formatTranscript(transcript));
      return;
    }

    case 'score': {
      const id = positional[0] || store.listSessions()[0]?.id;
      if (!id) return console.error('No sessions yet. Try `npm run demo`.');
      const scorecard = await scoreTranscript(id, { persona: safePersona(id) });
      console.log(`${scorecard.overall_score}/100 (${scorecard.scorer})`);
      for (const c of scorecard.categories) console.log(`  ${String(c.score).padStart(3)}  ${c.name}`);
      for (const f of scorecard.auto_fails) console.log(`  AUTO-FAIL: ${f}`);
      return;
    }

    case 'report': {
      const id = positional[0] || store.listSessions()[0]?.id;
      if (!id) return console.error('No sessions yet. Try `npm run demo`.');
      const files = renderReport(id);
      console.log(files.html);
      console.log(files.markdown);
      return;
    }

    case 'sessions': {
      const sessions = store.listSessions();
      if (!sessions.length) return console.log('No sessions yet. Try `npm run demo`.');
      for (const s of sessions) {
        console.log(`${s.id}  ${String(s.status).padEnd(15)} ${s.persona || '-'}  ${s.score != null ? s.score + '/100' : ''}`);
      }
      return;
    }

    default:
      console.log(USAGE);
      if (command) process.exitCode = 1;
  }
}

function safePersona(sessionId) {
  try { return loadPersona(store.getSession(sessionId).persona); } catch { return null; }
}

main().catch((err) => {
  console.error(`\nError: ${err.message}`);
  process.exitCode = 1;
});

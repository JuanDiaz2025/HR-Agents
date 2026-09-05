import { config, readJson } from './config.js';
import { complete, extractJson, activeProvider } from './llm.js';
import { formatTranscript } from './transcribe.js';
import * as store from './store.js';

export function loadRubric() {
  const rubric = readJson(config.rubricPath);
  const total = rubric.categories.reduce((n, c) => n + c.weight, 0);
  if (total !== 100) {
    console.warn(`[score] rubric "${rubric.id}" weights sum to ${total}, not 100 - scores will be normalized to a 100-point scale`);
  }
  return rubric;
}

/** Category weights are normalized so an authored rubric that does not sum to 100 still yields a 0-100 score. */
function weightFactor(rubric) {
  const total = rubric.categories.reduce((n, c) => n + c.weight, 0);
  return total > 0 ? 100 / total : 0;
}

const QUESTION_WORDS = ['what', 'why', 'how', 'when', 'who', 'where', 'which', 'can you', 'could you', 'would you', 'do you', 'did you', 'are you', 'is there', 'tell me', 'walk me'];
const MONEY = /\$\s?\d|\d{2,3}\s?(k\b|thousand)|\d{3},\d{3}|\bmillion\b/i;
const NEXT_STEP = /(tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next week|this week|at \d|schedule|calendar|call you back|come by|swing by|put you down)/i;

export function transcriptStats(transcript) {
  const turns = transcript.turns || [];
  const repTurns = turns.filter((t) => t.speaker === 'rep');
  const sellerTurns = turns.filter((t) => t.speaker === 'seller');
  const words = (list) => list.reduce((n, t) => n + t.text.split(/\s+/).filter(Boolean).length, 0);
  const repWords = words(repTurns);
  const sellerWords = words(sellerTurns);

  const questions = repTurns.flatMap((t) => t.text.split(/(?<=[.?!])\s+/))
    .filter((s) => s.includes('?') || QUESTION_WORDS.some((w) => s.toLowerCase().trim().startsWith(w)));

  return {
    rep_turns: repTurns.length,
    seller_turns: sellerTurns.length,
    rep_words: repWords,
    seller_words: sellerWords,
    rep_talk_pct: repWords + sellerWords ? Math.round((repWords / (repWords + sellerWords)) * 100) : 0,
    question_count: questions.length,
    questions: questions.map((q) => q.trim()).slice(0, 40),
  };
}

function detectAutoFails(transcript, stats) {
  const turns = transcript.turns || [];
  const fails = [];

  const firstMoney = turns.findIndex((t) => t.speaker === 'rep' && MONEY.test(t.text));
  const firstWhy = turns.findIndex((t) => t.speaker === 'rep' && /\b(why|what('s| is) (driving|going on)|what made you)\b/i.test(t.text));
  if (firstMoney !== -1 && (firstWhy === -1 || firstMoney < firstWhy)) {
    fails.push('Rep gave a specific dollar figure before asking why the seller is selling');
  }

  const lastRepTurns = turns.filter((t) => t.speaker === 'rep').slice(-4);
  if (!lastRepTurns.some((t) => NEXT_STEP.test(t.text))) {
    fails.push('Call ended without a specific next step');
  }

  return fails;
}

/**
 * Offline scorer. No credentials, no network - keyword and structure signals only.
 * It is deliberately coarse: it exists so the loop is testable end to end on day
 * one and so a grading outage never blocks a practice session. The LLM scorer is
 * the one that reads for quality.
 */
export function scoreOffline(transcript, rubric) {
  const factor = weightFactor(rubric);
  const stats = transcriptStats(transcript);
  const repText = (transcript.turns || []).filter((t) => t.speaker === 'rep').map((t) => t.text.toLowerCase()).join(' \n ');
  const repTurns = (transcript.turns || []).filter((t) => t.speaker === 'rep');

  const categories = rubric.categories.map((cat) => {
    let score;
    const evidence = [];
    const missed = [];
    // How this category's shortfall should be phrased as a coaching note. The
    // control category measures behavior directly, so its misses are already
    // full sentences; the others are unmet criteria and need framing.
    let gapReason = null;

    if (cat.id === 'control') {
      const talkScore = stats.rep_talk_pct <= 45 ? 100 : Math.max(0, 100 - (stats.rep_talk_pct - 45) * 3);
      const questionScore = Math.min(100, (stats.question_count / 8) * 100);
      score = Math.round(talkScore * 0.5 + questionScore * 0.5);
      evidence.push(`Rep talk time ${stats.rep_talk_pct}% (target: under 45%)`);
      evidence.push(`${stats.question_count} questions asked (target: 8+)`);
      if (stats.rep_talk_pct > 45) missed.push('Rep talked more than the target share of the call');
      if (stats.question_count < 8) missed.push('Fewer than 8 questions asked');
      gapReason = missed[0] || null;
    } else {
      const hits = (cat.signals || []).filter((s) => repText.includes(s));
      const target = Math.min((cat.signals || []).length, 3) || 1;
      score = Math.round(Math.min(1, hits.length / target) * 100);
      for (const hit of hits.slice(0, 3)) {
        const turn = repTurns.find((t) => t.text.toLowerCase().includes(hit));
        evidence.push(`"${(turn?.text || hit).slice(0, 140)}"`);
      }
      if (!hits.length) missed.push(...(cat.criteria || []).slice(0, 2));
      else if (hits.length < target) missed.push(...(cat.criteria || []).slice(hits.length, hits.length + 1));
      gapReason = missed[0] ? `No evidence in the transcript of: ${missed[0].toLowerCase()}` : null;
    }

    return { id: cat.id, name: cat.name, weight: cat.weight, score, weighted: +(score * cat.weight * factor / 100).toFixed(1), evidence, missed, gap_reason: gapReason };
  });

  const overall = Math.round(categories.reduce((n, c) => n + c.weighted, 0));
  const autoFails = detectAutoFails(transcript, stats);
  // Only categories with an actual shortfall can be an opportunity - a 100/100
  // category is not something to work on. Ranked by weighted points lost.
  const ranked = categories
    .filter((c) => c.score < 90)
    .sort((a, b) => ((100 - b.score) * b.weight) - ((100 - a.score) * a.weight));

  return {
    session_id: transcript.session_id || null,
    rubric_id: rubric.id,
    scored_at: new Date().toISOString(),
    scorer: 'offline-heuristic',
    scorer_note: 'Keyword and structure heuristics only. Directional, not a substitute for the LLM scorer.',
    overall_score: overall,
    passed: overall >= rubric.passing_score && autoFails.length === 0,
    stats,
    categories,
    auto_fails: autoFails,
    strengths: categories.filter((c) => c.score >= 80).map((c) => `${c.name}: ${c.score}/100`),
    top_opportunities: ranked.slice(0, 4).map((c, i) => ({
      rank: i + 1,
      title: c.name,
      why: c.gap_reason || `Scored ${c.score}/100 on a ${c.weight}-point category`,
      say_this_instead: null,
    })),
    next_call_focus: ranked[0]
      ? `Focus the next practice call on ${ranked[0].name} - it is costing ${(+((100 - ranked[0].score) * ranked[0].weight * factor / 100).toFixed(1))} points a call.`
      : 'No category scored below 90. Raise the difficulty or tighten the rubric.',
  };
}

const SCORER_SYSTEM = `You are a sales coach grading a recorded practice call for a residential real-estate acquisitions team.
You grade strictly against the rubric you are given, and you only credit behavior that actually appears in the transcript.
Quote the transcript as evidence. If a criterion was not met, say what the rep should have said instead, in words a rep could read aloud.
Never invent quotes. Return only JSON.`;

function scorerPrompt(transcript, rubric, persona) {
  const stats = transcriptStats(transcript);
  return `# Rubric
${JSON.stringify(rubric, null, 2)}

# Scenario the AI seller was playing
${persona ? `${persona.name} - ${persona.scenario?.headline}. Facts the rep could have uncovered: ${JSON.stringify(persona.scenario?.situation || {})}` : '(not recorded)'}

# Measured stats
Rep talk share: ${stats.rep_talk_pct}%. Questions asked by rep: ${stats.question_count}. Rep turns: ${stats.rep_turns}, seller turns: ${stats.seller_turns}.

# Transcript
${formatTranscript(transcript)}

# Task
Score every rubric category from 0 to 100. Then return JSON in exactly this shape:
{
  "categories": [{"id": "...", "score": 0-100, "evidence": ["direct quote from the transcript"], "missed": ["criterion not met"]}],
  "auto_fails": ["any auto_fail from the rubric that actually occurred, quoted"],
  "strengths": ["specific thing the rep did well, with a quote"],
  "top_opportunities": [{"rank": 1, "title": "...", "why": "...", "say_this_instead": "exact words the rep should use next time"}],
  "missed_information": ["fact about the seller the rep never uncovered"],
  "next_call_focus": "one sentence"
}
Include every category id from the rubric. Give at most 4 top_opportunities, ordered by how much they cost the deal.`;
}

/** LLM scorer with the offline scorer as the fallback when no provider is configured. */
export async function scoreTranscript(sessionId, { persona = null, rubric = loadRubric() } = {}) {
  store.getSession(sessionId); // throws a clear error if the session does not exist
  const transcript = store.readArtifact(sessionId, 'transcript.json');
  if (!transcript) throw new Error(`Session ${sessionId} has no transcript.json yet - run transcribe first`);

  let scorecard;
  if (activeProvider()) {
    try {
      const raw = await complete({ system: SCORER_SYSTEM, prompt: scorerPrompt(transcript, rubric, persona), temperature: 0.1 });
      scorecard = mergeLlmScores(extractJson(raw), transcript, rubric);
    } catch (err) {
      console.warn(`[score] LLM scoring failed (${err.message}); falling back to the offline scorer`);
      scorecard = scoreOffline(transcript, rubric);
      scorecard.scorer_note += ` LLM scoring failed: ${err.message}`;
    }
  } else {
    scorecard = scoreOffline(transcript, rubric);
  }

  scorecard.session_id = sessionId;
  store.saveArtifact(sessionId, 'scorecard.json', scorecard);
  return scorecard;
}

/** Recombine model-assigned category scores with the rubric weights we control. */
export function mergeLlmScores(result, transcript, rubric) {
  const factor = weightFactor(rubric);
  const byId = new Map((result.categories || []).map((c) => [c.id, c]));
  const categories = rubric.categories.map((cat) => {
    const scored = byId.get(cat.id) || {};
    const score = Math.max(0, Math.min(100, Number(scored.score ?? 0)));
    return {
      id: cat.id,
      name: cat.name,
      weight: cat.weight,
      score,
      weighted: +(score * cat.weight * factor / 100).toFixed(1),
      evidence: scored.evidence || [],
      missed: scored.missed || [],
    };
  });

  const overall = Math.round(categories.reduce((n, c) => n + c.weighted, 0));
  const autoFails = result.auto_fails || [];

  return {
    rubric_id: rubric.id,
    scored_at: new Date().toISOString(),
    scorer: `llm:${activeProvider()}`,
    scorer_note: '',
    overall_score: overall,
    passed: overall >= rubric.passing_score && autoFails.length === 0,
    stats: transcriptStats(transcript),
    categories,
    auto_fails: autoFails,
    strengths: result.strengths || [],
    top_opportunities: (result.top_opportunities || []).slice(0, 4),
    missed_information: result.missed_information || [],
    next_call_focus: result.next_call_focus || '',
  };
}

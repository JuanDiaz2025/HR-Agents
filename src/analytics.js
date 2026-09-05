import * as store from './store.js';
import { loadRubric } from './score.js';

/**
 * Roll every session folder up into the numbers the dashboard shows.
 * Reads straight off disk - there is no separate analytics store to keep in
 * sync, and re-scoring a session immediately changes what the dashboard says.
 */

const DAY = 24 * 60 * 60 * 1000;

function scorecardFor(session) {
  try { return store.readArtifact(session.id, 'scorecard.json'); } catch { return null; }
}

export function loadScoredSessions() {
  return store.listSessions()
    .map((session) => ({ session, scorecard: scorecardFor(session) }))
    .filter((row) => row.scorecard);
}

function average(numbers) {
  if (!numbers.length) return null;
  return Math.round(numbers.reduce((a, b) => a + b, 0) / numbers.length);
}

/** Per-rep history, oldest first, so a trend line reads left to right. */
export function repBreakdown(rows) {
  const byRep = new Map();

  for (const { session, scorecard } of rows) {
    const rep = session.rep || 'Unassigned';
    if (!byRep.has(rep)) byRep.set(rep, []);
    byRep.get(rep).push({
      session_id: session.id,
      date: session.created_at,
      score: scorecard.overall_score,
      passed: scorecard.passed,
      persona: session.persona,
      difficulty: session.difficulty,
      categories: scorecard.categories,
    });
  }

  return [...byRep.entries()].map(([rep, calls]) => {
    const ordered = [...calls].sort((a, b) => String(a.date).localeCompare(String(b.date)));
    const scores = ordered.map((c) => c.score);

    // Weakest category = the one costing this rep the most weighted points.
    const lost = new Map();
    for (const call of ordered) {
      for (const cat of call.categories || []) {
        const cost = (100 - cat.score) * cat.weight / 100;
        lost.set(cat.id, { name: cat.name, cost: (lost.get(cat.id)?.cost || 0) + cost });
      }
    }
    const weakest = [...lost.entries()].sort((a, b) => b[1].cost - a[1].cost)[0];

    // Improvement = last three calls against the first three.
    const early = scores.slice(0, 3);
    const late = scores.slice(-3);
    const trend = ordered.length >= 4 && average(late) !== null && average(early) !== null
      ? average(late) - average(early)
      : null;

    return {
      rep,
      calls: ordered.length,
      avg_score: average(scores),
      last_score: scores[scores.length - 1] ?? null,
      best_score: scores.length ? Math.max(...scores) : null,
      pass_rate: Math.round((ordered.filter((c) => c.passed).length / ordered.length) * 100),
      trend,
      weakest: weakest ? weakest[1].name : null,
      history: ordered.map((c) => ({ session_id: c.session_id, date: c.date, score: c.score, passed: c.passed, difficulty: c.difficulty })),
    };
  }).sort((a, b) => b.calls - a.calls);
}

/** Team-wide average per rubric category - where coaching pays off most. */
export function categoryBreakdown(rows) {
  const rubric = loadRubric();
  return rubric.categories.map((cat) => {
    const scores = rows
      .map(({ scorecard }) => scorecard.categories?.find((c) => c.id === cat.id)?.score)
      .filter((s) => typeof s === 'number');
    const avg = average(scores);
    return {
      id: cat.id,
      name: cat.name,
      weight: cat.weight,
      avg_score: avg,
      // Weighted points the team is leaving on the table on an average call.
      points_lost: avg === null ? 0 : +((100 - avg) * cat.weight / 100).toFixed(1),
      what_good_looks_like: cat.what_good_looks_like,
    };
  });
}

export function summarize() {
  const rows = loadScoredSessions();
  const now = Date.now();
  const scores = rows.map((r) => r.scorecard.overall_score);
  const thisWeek = rows.filter((r) => now - new Date(r.session.created_at).getTime() < 7 * DAY);

  const categories = categoryBreakdown(rows);
  const rubric = loadRubric();

  return {
    generated_at: new Date().toISOString(),
    totals: {
      calls: rows.length,
      calls_this_week: thisWeek.length,
      avg_score: average(scores),
      pass_rate: rows.length ? Math.round((rows.filter((r) => r.scorecard.passed).length / rows.length) * 100) : null,
      passing_score: rubric.passing_score,
      reps: new Set(rows.map((r) => r.session.rep || 'Unassigned')).size,
      auto_fails: rows.reduce((n, r) => n + (r.scorecard.auto_fails?.length || 0), 0),
    },
    categories,
    biggest_gaps: [...categories].sort((a, b) => b.points_lost - a.points_lost).slice(0, 3),
    reps: repBreakdown(rows),
    recent: rows.slice(0, 12).map(({ session, scorecard }) => ({
      id: session.id,
      date: session.created_at,
      rep: session.rep || 'Unassigned',
      persona: session.persona,
      difficulty: session.difficulty,
      score: scorecard.overall_score,
      passed: scorecard.passed,
      scorer: scorecard.scorer,
      auto_fails: scorecard.auto_fails?.length || 0,
      duration_seconds: session.duration_seconds || null,
    })),
  };
}

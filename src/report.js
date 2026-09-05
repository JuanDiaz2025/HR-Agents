import * as store from './store.js';
import { formatTranscript } from './transcribe.js';
import { loadPersona } from './persona.js';

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/** Status color role for a 0-100 category score. Always paired with a label, never color alone. */
function band(score) {
  if (score >= 85) return { role: 'good', label: 'Strong' };
  if (score >= 70) return { role: 'ok', label: 'Solid' };
  if (score >= 50) return { role: 'warning', label: 'Needs work' };
  return { role: 'critical', label: 'Gap' };
}

const STYLE = `
:root {
  color-scheme: light;
  --surface: #fcfcfb; --plane: #f9f9f7;
  --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --baseline: #c3c2b7; --border: rgba(11,11,11,0.10);
  --meter: #2a78d6; --meter-track: #e1e0d9;
  --good: #0ca30c; --ok: #2a78d6; --warning: #fab219; --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface: #1a1a19; --plane: #0d0d0d;
    --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,0.10);
    --meter: #3987e5; --meter-track: #2c2c2a;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--plane); color: var(--ink);
  font: 14px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
.wrap { max-width: 900px; margin: 0 auto; padding: 32px 20px 64px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 20px 22px; margin-bottom: 16px; }
h1 { font-size: 21px; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.07em; color: var(--muted); margin: 0 0 14px; font-weight: 600; }
.sub { color: var(--ink-2); font-size: 13px; margin: 0; }
.hero { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
.hero .num { font-size: 60px; font-weight: 650; line-height: 1; letter-spacing: -0.03em; font-variant-numeric: tabular-nums; }
.hero .den { font-size: 17px; color: var(--muted); }
.verdict { display: inline-flex; align-items: center; gap: 6px; font-weight: 600; font-size: 13px;
  border: 1px solid var(--border); border-radius: 999px; padding: 4px 11px; }
.verdict .dot { width: 9px; height: 9px; border-radius: 50%; }
.track { height: 8px; background: var(--meter-track); border-radius: 4px; overflow: hidden; margin-top: 14px; }
.track > i { display: block; height: 100%; background: var(--meter); border-radius: 4px; }
.kpis { display: flex; gap: 26px; flex-wrap: wrap; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--grid); }
.kpi .v { font-size: 21px; font-weight: 600; font-variant-numeric: tabular-nums; }
.kpi .k { font-size: 11.5px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); font-weight: 600; padding-bottom: 8px; }
td { padding: 9px 0; border-top: 1px solid var(--grid); vertical-align: middle; }
td.meter { width: 190px; }
td.val { width: 62px; text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
td.tag { width: 116px; padding-left: 14px; }
.bar { height: 8px; background: var(--meter-track); border-radius: 4px; overflow: hidden; }
.bar > i { display: block; height: 100%; border-radius: 4px; background: var(--meter); }
.tagline { font-size: 12px; display: inline-flex; align-items: center; gap: 5px; color: var(--ink-2); }
.tagline .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.dot-good { background: var(--good); } .dot-ok { background: var(--ok); }
.dot-warning { background: var(--warning); } .dot-critical { background: var(--critical); }
details { border-top: 1px solid var(--grid); }
details summary { cursor: pointer; padding: 9px 0; font-size: 12.5px; color: var(--ink-2); }
ol.opps { margin: 0; padding-left: 20px; }
ol.opps li { margin-bottom: 14px; }
ol.opps .why { color: var(--ink-2); }
.say { margin-top: 5px; padding: 9px 11px; background: var(--plane); border-left: 3px solid var(--meter); border-radius: 0 5px 5px 0; }
.say b { display: block; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin-bottom: 3px; }
ul.plain { margin: 0; padding-left: 18px; } ul.plain li { margin-bottom: 6px; }
.alert { border-left: 3px solid var(--critical); padding: 9px 12px; background: var(--plane); border-radius: 0 5px 5px 0; margin-bottom: 8px; }
pre.transcript { white-space: pre-wrap; font: 12.5px/1.65 ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--plane); border: 1px solid var(--border); border-radius: 6px; padding: 14px; overflow-x: auto; margin: 0; }
footer { color: var(--muted); font-size: 12px; text-align: center; margin-top: 26px; }
@media print { body { background: #fff; } .card { break-inside: avoid; border-color: #ddd; } details { display: none; } }
`;

export function renderHtml({ session, scorecard, transcript, persona }) {
  const s = scorecard.stats || {};
  const verdict = scorecard.passed
    ? { role: 'good', label: 'Meets the standard' }
    : { role: 'critical', label: 'Below the standard' };

  const rows = scorecard.categories.map((c) => {
    const b = band(c.score);
    const detail = [...(c.evidence || []).map((e) => `<li>${esc(e)}</li>`), ...(c.missed || []).map((m) => `<li>Missed: ${esc(m)}</li>`)].join('');
    return `<tr>
      <td>${esc(c.name)} <span style="color:var(--muted);font-size:12px">(${c.weight} pts)</span></td>
      <td class="meter"><div class="bar" role="img" aria-label="${esc(c.name)} scored ${c.score} of 100"><i style="width:${c.score}%"></i></div></td>
      <td class="val">${c.score}</td>
      <td class="tag"><span class="tagline"><span class="dot dot-${b.role}"></span>${b.label}</span></td>
    </tr>
    ${detail ? `<tr><td colspan="4" style="border-top:none;padding:0"><details><summary>Evidence &amp; gaps</summary><ul class="plain">${detail}</ul></details></td></tr>` : ''}`;
  }).join('');

  const opportunities = (scorecard.top_opportunities || []).map((o) => `<li>
      <b>${esc(o.title)}</b><div class="why">${esc(o.why)}</div>
      ${o.say_this_instead ? `<div class="say"><b>Say this instead</b>${esc(o.say_this_instead)}</div>` : ''}
    </li>`).join('');

  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Practice call scorecard - ${esc(session.id)}</title>
<style>${STYLE}</style></head>
<body><div class="wrap">

<div class="card">
  <h1>Practice Call Scorecard</h1>
  <p class="sub">${esc(session.id)} &middot; ${esc(new Date(scorecard.scored_at).toLocaleString())}
    &middot; rep: ${esc(session.rep || 'unassigned')}
    &middot; seller: ${esc(persona?.name || session.persona || 'unknown')} (${esc(session.difficulty || persona?.difficulty || 'medium')})</p>
  <div class="hero" style="margin-top:20px">
    <span class="num">${scorecard.overall_score}</span><span class="den">/ 100</span>
    <span class="verdict"><span class="dot dot-${verdict.role}"></span>${verdict.label}</span>
  </div>
  <div class="track" role="img" aria-label="Overall score ${scorecard.overall_score} of 100"><i style="width:${scorecard.overall_score}%"></i></div>
  <div class="kpis">
    <div class="kpi"><div class="v">${s.rep_talk_pct ?? '-'}%</div><div class="k">Rep talk time</div></div>
    <div class="kpi"><div class="v">${s.question_count ?? '-'}</div><div class="k">Questions asked</div></div>
    <div class="kpi"><div class="v">${session.duration_seconds ? Math.round(session.duration_seconds / 60) + 'm' : '-'}</div><div class="k">Call length</div></div>
    <div class="kpi"><div class="v">${s.rep_turns ?? '-'}</div><div class="k">Rep turns</div></div>
  </div>
</div>

${scorecard.auto_fails?.length ? `<div class="card"><h2>Auto-fails</h2>${scorecard.auto_fails.map((f) => `<div class="alert">${esc(f)}</div>`).join('')}</div>` : ''}

<div class="card"><h2>Score by category</h2>
  <table><thead><tr><th>Category</th><th>Score</th><th class="val">/100</th><th style="padding-left:14px">Band</th></tr></thead>
  <tbody>${rows}</tbody></table>
</div>

${opportunities ? `<div class="card"><h2>Top opportunities</h2><ol class="opps">${opportunities}</ol></div>` : ''}

${scorecard.strengths?.length ? `<div class="card"><h2>What worked</h2><ul class="plain">${scorecard.strengths.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}

${scorecard.missed_information?.length ? `<div class="card"><h2>Information never uncovered</h2><ul class="plain">${scorecard.missed_information.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}

${scorecard.next_call_focus ? `<div class="card"><h2>Focus for the next call</h2><p style="margin:0">${esc(scorecard.next_call_focus)}</p></div>` : ''}

<div class="card"><h2>Transcript</h2><pre class="transcript">${esc(transcript ? formatTranscript(transcript) : 'No transcript available')}</pre></div>

<footer>Scored by ${esc(scorecard.scorer)} against rubric ${esc(scorecard.rubric_id)}.${scorecard.scorer_note ? ' ' + esc(scorecard.scorer_note) : ''}</footer>
</div></body></html>`;
}

export function renderMarkdown({ session, scorecard }) {
  const lines = [
    `# Practice Call Scorecard - ${session.id}`,
    '',
    `**${scorecard.overall_score}/100** - ${scorecard.passed ? 'meets the standard' : 'below the standard'}`,
    `Rep: ${session.rep || 'unassigned'} | Seller: ${session.persona || 'unknown'} | Scored by: ${scorecard.scorer}`,
    '',
    `Rep talk time ${scorecard.stats?.rep_talk_pct}% | Questions asked ${scorecard.stats?.question_count}`,
    '',
    '## Score by category',
    '',
    '| Category | Weight | Score |',
    '| --- | ---: | ---: |',
    ...scorecard.categories.map((c) => `| ${c.name} | ${c.weight} | ${c.score} |`),
    '',
  ];
  if (scorecard.auto_fails?.length) lines.push('## Auto-fails', '', ...scorecard.auto_fails.map((f) => `- ${f}`), '');
  if (scorecard.top_opportunities?.length) {
    lines.push('## Top opportunities', '');
    for (const o of scorecard.top_opportunities) {
      lines.push(`${o.rank}. **${o.title}** - ${o.why}`);
      if (o.say_this_instead) lines.push(`   > Say instead: "${o.say_this_instead}"`);
    }
    lines.push('');
  }
  if (scorecard.next_call_focus) lines.push('## Focus for the next call', '', scorecard.next_call_focus, '');
  return lines.join('\n');
}

export function renderReport(sessionId) {
  const session = store.getSession(sessionId);
  const scorecard = store.readArtifact(sessionId, 'scorecard.json');
  if (!scorecard) throw new Error(`No scorecard.json for session ${sessionId} - run score first`);
  const transcript = store.readArtifact(sessionId, 'transcript.json');
  let persona = null;
  try { persona = session.persona ? loadPersona(session.persona) : null; } catch {}

  const html = renderHtml({ session, scorecard, transcript, persona });
  const md = renderMarkdown({ session, scorecard });
  return {
    html: store.saveArtifact(sessionId, 'report.html', html),
    markdown: store.saveArtifact(sessionId, 'report.md', md),
  };
}

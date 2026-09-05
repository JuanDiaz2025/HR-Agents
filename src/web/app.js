// Dashboard for the AI Sales Practice & Testing System.
// No framework and no build step on purpose: this is a small internal tool, and
// "npm run serve, open the browser" is the whole setup story.

const app = document.getElementById('app');
const tooltip = document.getElementById('tooltip');

// A ?token=... in the URL is stored as a cookie so every later fetch carries it.
const urlToken = new URLSearchParams(location.search).get('token');
if (urlToken) {
  document.cookie = `practice_token=${encodeURIComponent(urlToken)}; path=/; SameSite=Lax`;
  history.replaceState({}, '', location.pathname + location.hash);
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// A snapshot export (`npm run snapshot`) inlines the API responses so the whole
// dashboard works as one static file with no server behind it.
const SNAPSHOT = window.__PRACTICE_SNAPSHOT__ || null;

// Running as a packaged .exe there is no npm and no shell - the instructions
// this app gives about itself have to match how it was actually started.
let packaged = null;
const seedCommand = () => packaged ? 'AI-Sales-Practice.exe demo --seed 12' : 'npm run demo -- --seed 12';
const clearCommand = () => packaged ? 'deleting the data folder next to the app' : 'rm -rf data/';

async function api(path, options) {
  if (SNAPSHOT) {
    if (options?.method === 'POST') throw new Error('This is a static snapshot - run the server to place a real call.');
    const hit = SNAPSHOT[path];
    if (!hit) throw new Error(`Not in this snapshot: ${path}`);
    return hit;
  }
  const res = await fetch(`/api/${path}`, { headers: { 'Content-Type': 'application/json' }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `Request failed (${res.status})`);
  return body;
}

function band(score) {
  if (score == null) return { role: 'muted', label: '-' };
  if (score >= 85) return { role: 'good', label: 'Strong' };
  if (score >= 70) return { role: 'ok', label: 'Solid' };
  if (score >= 50) return { role: 'warning', label: 'Needs work' };
  return { role: 'critical', label: 'Gap' };
}

const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '-';
const fmtDateTime = (iso) => iso ? new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '-';
const fmtDuration = (s) => s ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s` : '-';

const kpi = (value, label, note = '') =>
  `<div class="kpi"><div class="v">${value}</div><div class="k">${esc(label)}</div>${note ? `<div class="note">${esc(note)}</div>` : ''}</div>`;

const scorePill = (score, passed) => {
  const b = passed === undefined ? band(score) : (passed ? { role: 'good', label: 'Pass' } : { role: 'critical', label: 'Below' });
  return `<span class="pill"><span class="dot dot-${b.role}"></span>${score ?? '-'} ${b.label}</span>`;
};

const meter = (score) => `<div class="bar"><i style="width:${Math.max(0, Math.min(100, score || 0))}%"></i></div>`;

function empty(message, hint = '') {
  return `<div class="card"><div class="empty"><p>${message}</p>${hint ? `<p class="small">${hint}</p>` : ''}</div></div>`;
}

/* ---------------------------------------------------------------------------
   Score-over-time chart. One series, so no legend - the heading names it.
   Points are direct-labeled on hover rather than every value being printed.
--------------------------------------------------------------------------- */
function lineChart(history, passingScore) {
  if (!history.length) return '<p class="muted small">No calls yet.</p>';
  if (history.length === 1) {
    return `<p class="muted small">One call so far, scored ${history[0].score}. A trend needs at least two.</p>`;
  }

  const W = 400, H = 180, padL = 26, padR = 10, padT = 14, padB = 18;
  const x = (i) => padL + (i / (history.length - 1)) * (W - padL - padR);
  const y = (v) => padT + (1 - v / 100) * (H - padT - padB);

  const gridlines = [0, 50, 100].map((v) =>
    `<line class="gridline" x1="${padL}" x2="${W - padR}" y1="${y(v)}" y2="${y(v)}"></line>
     <text class="lbl" x="0" y="${y(v) + 3.5}">${v}</text>`).join('');

  const path = history.map((h, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(h.score).toFixed(1)}`).join(' ');

  const points = history.map((h, i) =>
    `<circle class="pt" cx="${x(i).toFixed(1)}" cy="${y(h.score).toFixed(1)}" r="5"
       data-tip="${esc(fmtDate(h.date))} &middot; ${h.score}/100 &middot; ${esc(h.difficulty || '')}"
       data-session="${esc(h.session_id)}"></circle>`).join('');

  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Score over ${history.length} calls, from ${history[0].score} to ${history[history.length - 1].score} out of 100">
    ${gridlines}
    <line class="target" x1="${padL}" x2="${W - padR}" y1="${y(passingScore)}" y2="${y(passingScore)}"></line>
    <path class="line" d="${path}"></path>
    ${points}
  </svg>
  <p class="small muted" style="margin:2px 0 0">Dashed line is the ${passingScore} standard. ${history.length} calls, ${esc(fmtDate(history[0].date))} to ${esc(fmtDate(history[history.length - 1].date))}.</p>`;
}

function wireChartTooltips(root) {
  for (const pt of root.querySelectorAll('.chart .pt')) {
    pt.addEventListener('mouseenter', (e) => {
      tooltip.innerHTML = pt.dataset.tip;
      tooltip.classList.add('on');
      const r = e.target.getBoundingClientRect();
      tooltip.style.left = `${r.left + r.width / 2 - tooltip.offsetWidth / 2}px`;
      tooltip.style.top = `${r.top - tooltip.offsetHeight - 8}px`;
    });
    pt.addEventListener('mouseleave', () => tooltip.classList.remove('on'));
    pt.addEventListener('click', () => { location.hash = `#/sessions/${pt.dataset.session}`; });
  }
}

function wireRowLinks(root) {
  for (const tr of root.querySelectorAll('tr.click[data-href]')) {
    tr.addEventListener('click', () => { location.hash = tr.dataset.href; });
  }
}

/* --------------------------------- Views --------------------------------- */

/** Sample sessions carry a flag, so a snapshot can say plainly that these are not real numbers. */
async function sampleDataBanner() {
  const { sessions } = await api('sessions');
  const samples = sessions.filter((s) => s.sample_data).length;
  if (!samples) return '';
  return `<div class="banner"><b>Sample data &mdash; not real calls</b>
    ${samples} of these ${sessions.length} sessions were generated by <code>${esc(seedCommand())}</code> so the dashboard has
    something to show. The rep names are invented. Scores are real in the sense that every one came from the actual scorer
    running on an actual transcript &mdash; but no phone ever rang. Clear them by ${esc(clearCommand())}.</div>`;
}

async function viewDashboard() {
  const [data, banner] = await Promise.all([api('summary'), sampleDataBanner()]);
  packaged = Boolean(data.packaged);
  const t = data.totals;

  if (!t.calls) {
    return {
      html: empty(
        'No scored calls yet.',
        `Run <code>${esc(seedCommand())}</code> to fill this in with sample calls, or start a real one from the "Start a call" tab.`,
      ),
    };
  }

  const gaps = data.biggest_gaps.map((g) => `<tr>
      <td>${esc(g.name)}<div class="small muted">${esc(g.what_good_looks_like || '')}</div></td>
      <td style="width:170px">${meter(g.avg_score)}</td>
      <td class="num" style="width:52px"><b>${g.avg_score ?? '-'}</b></td>
      <td class="num small muted" style="width:120px">-${g.points_lost} pts / call</td>
    </tr>`).join('');

  const recent = data.recent.map((r) => `<tr class="click" data-href="#/sessions/${esc(r.id)}">
      <td>${esc(fmtDateTime(r.date))}</td>
      <td>${esc(r.rep)}</td>
      <td class="small muted">${esc(r.persona || '-')} / ${esc(r.difficulty || '-')}</td>
      <td class="num">${scorePill(r.score, r.passed)}</td>
      <td class="num small muted">${r.auto_fails ? `${r.auto_fails} auto-fail` : ''}</td>
    </tr>`).join('');

  return {
    html: `
      <h1>Team dashboard</h1>
      <p class="sub">${t.calls} scored call${t.calls === 1 ? '' : 's'} across ${t.reps} rep${t.reps === 1 ? '' : 's'}. The standard is ${t.passing_score}/100.</p>
      ${banner}

      <div class="card"><div class="kpis">
        ${kpi(t.avg_score ?? '-', 'Average score', `standard is ${t.passing_score}`)}
        ${kpi(`${t.pass_rate ?? 0}%`, 'Pass rate')}
        ${kpi(t.calls_this_week, 'Calls this week')}
        ${kpi(t.reps, 'Reps practicing')}
        ${kpi(t.auto_fails, 'Auto-fails', 'no next step, or price too early')}
      </div></div>

      <div class="card">
        <h2>Where the team is losing the most points</h2>
        <table><tbody>${gaps}</tbody></table>
        <p class="small muted" style="margin:14px 0 0">Ranked by weighted points lost on an average call, so a small gap in a heavy category outranks a big gap in a light one.</p>
      </div>

      <div class="card">
        <h2>Recent calls</h2>
        <table>
          <thead><tr><th>When</th><th>Rep</th><th>Scenario</th><th class="num">Score</th><th></th></tr></thead>
          <tbody>${recent}</tbody>
        </table>
      </div>`,
    after: wireRowLinks,
  };
}

async function viewReps() {
  const [{ reps }, summary, banner] = await Promise.all([api('reps'), api('summary'), sampleDataBanner()]);
  if (!reps.length) return { html: empty('No scored calls yet.', 'Scores are grouped by the <code>--rep</code> name you pass when starting a call.') };

  const cards = reps.map((r) => {
    const arrow = r.trend == null ? '' : r.trend > 0 ? `<span class="pill"><span class="dot dot-good"></span>+${r.trend} improving</span>`
      : r.trend < 0 ? `<span class="pill"><span class="dot dot-critical"></span>${r.trend} slipping</span>`
      : `<span class="pill"><span class="dot dot-ok"></span>flat</span>`;
    return `<div class="card">
      <div style="display:flex;justify-content:space-between;align-items:start;gap:12px;flex-wrap:wrap">
        <div><h3>${esc(r.rep)}</h3><p class="small muted" style="margin:0">${r.calls} call${r.calls === 1 ? '' : 's'} &middot; ${r.pass_rate}% at standard</p></div>
        <div>${arrow}</div>
      </div>
      <div class="kpis tight" style="margin:16px 0 10px">
        ${kpi(r.avg_score ?? '-', 'Average')}
        ${kpi(r.last_score ?? '-', 'Last call')}
        ${kpi(r.best_score ?? '-', 'Best')}
      </div>
      ${lineChart(r.history, summary.totals.passing_score)}
      ${r.weakest ? `<p class="small" style="margin:12px 0 0"><span class="muted">Costing them the most:</span> <b>${esc(r.weakest)}</b></p>` : ''}
    </div>`;
  }).join('');

  return {
    html: `<h1>Reps</h1><p class="sub">Score per call, oldest first. Click a point to open that call.</p>${banner}<div class="grid-2">${cards}</div>`,
    after: wireChartTooltips,
  };
}

async function viewSessions() {
  const { sessions } = await api('sessions');
  if (!sessions.length) return { html: empty('No practice sessions yet.', 'Try <code>npm run demo</code>.') };

  const rows = sessions.map((s) => `<tr class="click" data-href="#/sessions/${esc(s.id)}">
      <td class="small">${esc(fmtDateTime(s.created_at))}</td>
      <td>${esc(s.rep || 'Unassigned')}</td>
      <td class="small muted">${esc(s.persona || '-')} / ${esc(s.difficulty || '-')}</td>
      <td class="small">${esc(s.status)}</td>
      <td class="small muted">${esc(fmtDuration(s.duration_seconds))}</td>
      <td class="num">${s.score != null ? scorePill(s.score) : '<span class="muted small">-</span>'}</td>
    </tr>`).join('');

  return {
    html: `<h1>All calls</h1><p class="sub">${sessions.length} session${sessions.length === 1 ? '' : 's'}, newest first.</p>
      <div class="card"><table>
        <thead><tr><th>When</th><th>Rep</th><th>Scenario</th><th>Status</th><th>Length</th><th class="num">Score</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`,
    after: wireRowLinks,
  };
}

async function viewSession(id) {
  const data = await api(`sessions/${encodeURIComponent(id)}`);
  const { session, scorecard } = data;

  if (!scorecard) {
    return {
      html: `<h1>${esc(session.id)}</h1>
        <p class="sub">${esc(session.rep || 'Unassigned')} &middot; ${esc(fmtDateTime(session.created_at))}</p>
        <div class="banner"><b>Not scored yet</b>Status is "${esc(session.status)}". Once the call ends the server transcribes and scores it automatically.</div>`,
    };
  }

  const cats = scorecard.categories.map((c) => {
    const b = band(c.score);
    return `<tr>
      <td>${esc(c.name)} <span class="muted small">(${c.weight} pts)</span></td>
      <td style="width:180px">${meter(c.score)}</td>
      <td class="num" style="width:48px"><b>${c.score}</b></td>
      <td style="width:118px;padding-left:12px"><span class="pill"><span class="dot dot-${b.role}"></span>${b.label}</span></td>
    </tr>`;
  }).join('');

  const opps = (scorecard.top_opportunities || []).map((o) => `<li style="margin-bottom:12px">
      <b>${esc(o.title)}</b><div class="muted small">${esc(o.why)}</div>
      ${o.say_this_instead ? `<div class="small" style="margin-top:4px"><span class="muted">Say instead:</span> &ldquo;${esc(o.say_this_instead)}&rdquo;</div>` : ''}
    </li>`).join('');

  return {
    html: `
      <h1>${esc(session.rep || 'Unassigned')} &middot; ${scorecard.overall_score}/100</h1>
      <p class="sub">${esc(session.id)} &middot; ${esc(fmtDateTime(session.created_at))} &middot;
        ${esc(session.persona || '-')} (${esc(session.difficulty || '-')}) &middot; scored by ${esc(scorecard.scorer)}</p>

      ${scorecard.auto_fails?.length ? `<div class="banner bad"><b>Auto-fail</b>${scorecard.auto_fails.map(esc).join('<br>')}</div>` : ''}

      <div class="card"><div class="kpis">
        ${kpi(scorecard.overall_score, 'Score', scorecard.passed ? 'meets the standard' : 'below the standard')}
        ${kpi(`${scorecard.stats?.rep_talk_pct ?? '-'}%`, 'Rep talk time', 'target: under 45%')}
        ${kpi(scorecard.stats?.question_count ?? '-', 'Questions asked', 'target: 8+')}
        ${kpi(fmtDuration(session.duration_seconds), 'Length')}
      </div></div>

      <div class="card"><h2>Score by category</h2><table><tbody>${cats}</tbody></table></div>
      ${opps ? `<div class="card"><h2>Top opportunities</h2><ol style="margin:0;padding-left:20px">${opps}</ol></div>` : ''}

      <div class="card">
        <h2>Full report</h2>
        <p class="small muted" style="margin:-6px 0 12px">Includes the evidence quotes and the whole transcript${
          SNAPSHOT ? '.' : `. <a href="/report/${esc(session.id)}" target="_blank" rel="noopener">Open in a new tab</a> to print or send it.`}</p>
        ${data.report_html
          ? `<iframe class="report" srcdoc="${esc(data.report_html)}" title="Full scorecard"></iframe>`
          : `<iframe class="report" src="/report/${esc(session.id)}" title="Full scorecard"></iframe>`}
      </div>`,
  };
}

async function viewPersonas() {
  const { personas } = await api('personas');
  const cards = personas.map((p) => `<div class="card">
      <h3>${esc(p.name)} <span class="muted small">v${p.version}</span></h3>
      <p class="small muted" style="margin:2px 0 12px">${esc(p.headline || '')}</p>
      <div class="kpis">
        ${kpi(p.objections, 'Objections')}
        ${kpi(p.reveal_rules, 'Hidden facts', 'only revealed if asked')}
        ${kpi(esc(p.difficulty), 'Default')}
      </div>
    </div>`).join('');

  return {
    html: `<h1>Seller personas</h1>
      <p class="sub">Each seller is one JSON file in <code>config/personas/</code>. Copy Larry to add another scenario &mdash; no code changes.</p>
      <div class="grid-2">${cards}</div>
      <div class="card"><h2>Why "hidden facts" matter</h2>
        <p style="margin:0" class="small">A persona only gives up certain facts when the rep asks the right question &mdash; Larry's sister is
        on title, and a rep who never asks who else is involved never hears about her. That is what makes the score mean
        something instead of being a vocabulary quiz.</p></div>`,
  };
}

async function viewLaunch() {
  const [summary, { personas }] = await Promise.all([api('summary'), api('personas')]);
  const canCall = summary.capabilities.live_calls && summary.can_launch_calls;

  let notice = '';
  if (!summary.capabilities.live_calls) {
    notice = `<div class="banner"><b>Live calls are not configured yet</b>
      Add your Twilio credentials, an OpenAI key with Realtime access, and a public <code>PUBLIC_URL</code> to <code>.env</code>.
      The <a href="#/connections">Connections tab</a> lists exactly what is missing.</div>`;
  } else if (!summary.can_launch_calls) {
    notice = `<div class="banner bad"><b>Launching calls is disabled</b>
      This dashboard is reachable at a public URL with no <code>DASHBOARD_TOKEN</code> set, so anyone with the link could
      spend your call budget. Set one in <code>.env</code>, restart, and open the dashboard with <code>?token=...</code>.</div>`;
  }

  const options = personas.map((p) => `<option value="${esc(p.id)}">${esc(p.name)} &mdash; ${esc(p.headline || '')}</option>`).join('');

  return {
    html: `
      <h1>Start a practice call</h1>
      <p class="sub">The AI seller dials your sales line. Answer it and work the call like a real lead.</p>
      ${notice}
      <div class="card" style="max-width:520px">
        <form id="launch-form">
          <div class="field"><label for="rep">Who is taking the call</label>
            <input id="rep" name="rep" placeholder="Marcus" autocomplete="off" required></div>
          <div class="field"><label for="persona">Seller</label>
            <select id="persona" name="persona">${options}</select></div>
          <div class="field"><label for="difficulty">Difficulty</label>
            <select id="difficulty" name="difficulty">
              <option value="easy">Easy &mdash; volunteers information, one objection</option>
              <option value="medium" selected>Medium &mdash; answers what is asked, a few objections</option>
              <option value="hard">Hard &mdash; deflects, objects often, has to be earned</option>
            </select></div>
          <div class="field"><label for="to">Number to call</label>
            <input id="to" name="to" placeholder="+14155550134" inputmode="tel" autocomplete="off">
            <p class="small muted" style="margin:6px 0 0">Leave blank to use <code>SALES_LINE_NUMBER</code> from .env.</p></div>
          <button type="submit" ${canCall ? '' : 'disabled'}>Call now</button>
        </form>
        <div id="launch-result" style="margin-top:16px"></div>
      </div>`,
    after(root) {
      const form = root.querySelector('#launch-form');
      const result = root.querySelector('#launch-result');
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const button = form.querySelector('button');
        button.disabled = true;
        button.textContent = 'Dialing...';
        result.innerHTML = '';
        try {
          const body = Object.fromEntries(new FormData(form));
          const r = await api('calls', { method: 'POST', body: JSON.stringify(body) });
          result.innerHTML = `<div class="banner good"><b>Calling ${esc(r.to)}</b>
            Answer the phone. When the call ends it is transcribed and scored automatically.<br>
            <a href="#/sessions/${esc(r.session_id)}">Open this session</a></div>`;
          form.reset();
        } catch (err) {
          result.innerHTML = `<div class="banner bad"><b>Could not place the call</b>${esc(err.message)}</div>`;
        } finally {
          button.disabled = !canCall;
          button.textContent = 'Call now';
        }
      });
    },
  };
}

const STATUS_STYLE = {
  ok: { role: 'good', label: 'Working' },
  warning: { role: 'warning', label: 'Check this' },
  error: { role: 'critical', label: 'Not working' },
  not_set: { role: 'muted', label: 'Not set' },
};

function connectionRow(key, r) {
  const st = STATUS_STYLE[r.status] || STATUS_STYLE.not_set;
  return `<tr>
    <td style="width:190px"><b>${esc(r.name)}</b><div class="small muted">${esc(r.purpose)}</div></td>
    <td style="width:130px"><span class="pill"><span class="dot dot-${st.role}"></span>${st.label}</span></td>
    <td class="small">${esc(r.detail || '')}${r.fix ? `<div class="muted" style="margin-top:3px">${esc(r.fix)}</div>` : ''}</td>
  </tr>`;
}

async function viewConnections() {
  const [{ fields, can_edit }, tested] = await Promise.all([api('connections'), api('connections/test', { method: 'POST' }).catch((e) => ({ error: e.message }))]);

  const inputs = fields.map((f) => `<div class="field">
      <label for="f-${esc(f.key)}">${esc(f.label)}</label>
      <input id="f-${esc(f.key)}" name="${esc(f.key)}" type="${f.secret ? 'password' : 'text'}"
        placeholder="${esc(f.set ? f.display : (f.placeholder || ''))}" autocomplete="off" spellcheck="false">
      <p class="small muted" style="margin:5px 0 0">${esc(f.where)}</p>
    </div>`).join('');

  const status = tested.error
    ? `<div class="banner bad"><b>Could not test the connections</b>${esc(tested.error)}</div>`
    : `<div class="card">
        <h2>Connection status</h2>
        <table><tbody>${Object.entries(tested.results).map(([k, r]) => connectionRow(k, r)).join('')}</tbody></table>
        <div style="margin-top:16px">${tested.ready_to_call
          ? `<div class="banner good"><b>Ready to place a real call</b>Everything a call needs is connected and answering.
              <a href="#/launch">Start a practice call</a>.</div>`
          : `<div class="banner"><b>Not ready to call yet</b>Still needed: ${esc(tested.blocking.join(', '))}.
              Fill those in below and press Save &amp; test &mdash; each one is checked against the real service.</div>`}
        </div>
        <button class="ghost" id="retest" style="margin-top:4px">Test again</button>
      </div>`;

  return {
    html: `
      <h1>Connections</h1>
      <p class="sub">Six outside services. Four of them are required before a phone can ring.
        Every check below makes a real request &mdash; "filled in" and "working" are not the same thing.</p>
      ${status}
      <div class="card" style="max-width:640px">
        <h2>Credentials</h2>
        ${can_edit ? '' : `<div class="banner bad"><b>Read only</b>This dashboard is on a public URL without a password, so credentials cannot be edited here. Set <code>DASHBOARD_TOKEN</code> in <code>.env</code>, or edit <code>.env</code> directly.</div>`}
        <p class="small muted" style="margin:-4px 0 16px">Saved to <code>.env</code> on this machine, never sent anywhere else.
          Leave a field blank to keep what is already there.</p>
        <form id="conn-form">${inputs}
          <button type="submit" ${can_edit ? '' : 'disabled'}>Save &amp; test</button>
        </form>
        <div id="conn-result" style="margin-top:16px"></div>
      </div>

      <div class="card"><h2>What each one costs</h2>
        <table>
          <thead><tr><th>Service</th><th>What it does</th><th class="num">Rough cost</th></tr></thead>
          <tbody>
            <tr><td>Twilio</td><td class="small">Places the call, records it</td><td class="num small">~$1/mo + ~1.3&cent;/min</td></tr>
            <tr><td>OpenAI</td><td class="small">The seller's voice, and transcription</td><td class="num small">~$2-4 per 10-min call</td></tr>
            <tr><td>Anthropic</td><td class="small">Scoring and coaching</td><td class="num small">pennies per call</td></tr>
            <tr><td>ngrok</td><td class="small">Public URL while testing</td><td class="num small">free</td></tr>
          </tbody>
        </table>
        <p class="small muted" style="margin:14px 0 0">Voice minutes are almost the entire bill. Verify current pricing on each provider's page &mdash; these are estimates.</p>
      </div>`,
    after(root) {
      root.querySelector('#retest')?.addEventListener('click', () => render());

      const form = root.querySelector('#conn-form');
      const result = root.querySelector('#conn-result');
      form?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const button = form.querySelector('button');
        button.disabled = true;
        button.textContent = 'Saving and testing...';
        try {
          const body = Object.fromEntries([...new FormData(form)].filter(([, v]) => String(v).trim()));
          if (!Object.keys(body).length) throw new Error('Nothing to save - fill in at least one field.');
          const r = await api('connections', { method: 'POST', body: JSON.stringify(body) });
          result.innerHTML = `<div class="banner good"><b>Saved ${r.saved.length} credential${r.saved.length === 1 ? '' : 's'}</b>
            ${r.connections.ready_to_call
              ? 'Everything a call needs is now connected. <a href="#/launch">Start a practice call</a>.'
              : `Still needed: ${esc(r.connections.blocking.join(', '))}.`}</div>`;
          form.reset();
          setTimeout(render, 1200);
        } catch (err) {
          result.innerHTML = `<div class="banner bad"><b>Could not save</b>${esc(err.message)}</div>`;
        } finally {
          button.disabled = false;
          button.textContent = 'Save & test';
        }
      });
    },
  };
}

/* --------------------------------- Router -------------------------------- */

const routes = [
  [/^$/, viewDashboard],
  [/^reps$/, viewReps],
  [/^sessions$/, viewSessions],
  [/^sessions\/(.+)$/, viewSession],
  [/^personas$/, viewPersonas],
  [/^launch$/, viewLaunch],
  [/^connections$/, viewConnections],
];

async function render() {
  const hash = location.hash.replace(/^#\/?/, '');
  // Every view's wording depends on this and any view can be the entry point,
  // so resolve it once on the first render rather than per navigation.
  if (!SNAPSHOT && packaged === null) {
    try { packaged = Boolean((await api('summary')).packaged); } catch { packaged = false; }
  }
  const match = routes.map(([re, view]) => [re.exec(hash), view]).find(([m]) => m);

  for (const link of document.querySelectorAll('#nav a')) {
    link.classList.toggle('active', link.dataset.route === hash.split('/')[0]);
  }

  if (!match) {
    app.innerHTML = empty('Page not found.', '<a href="#/">Back to the dashboard</a>');
    return;
  }

  app.innerHTML = '<div class="empty">Loading&hellip;</div>';
  try {
    const { html, after } = await match[1](...match[0].slice(1));
    app.innerHTML = html;
    if (after) after(app);
    window.scrollTo(0, 0);
  } catch (err) {
    app.innerHTML = `<div class="card"><div class="banner bad"><b>Something went wrong</b>${esc(err.message)}</div></div>`;
  }
}

window.addEventListener('hashchange', render);
render();

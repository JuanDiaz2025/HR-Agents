#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, capabilities } from '../src/config.js';
import * as store from '../src/store.js';
import { summarize, repBreakdown, loadScoredSessions } from '../src/analytics.js';
import { listPersonas, loadPersona } from '../src/persona.js';
import { loadRubric } from '../src/score.js';

/**
 * Export the dashboard as one self-contained HTML file with the current data
 * inlined - no server, no API calls. Useful for sending a read-only view of the
 * team's numbers to someone who is not going to clone a repo, and for looking at
 * the dashboard from a phone.
 *
 * Everything that spends money or reveals credentials is left out by
 * construction: the snapshot contains only what the read-only endpoints return.
 */
const WEB = path.join(ROOT, 'src', 'web');

function buildSnapshotData() {
  const data = {
    summary: { ...summarize(), capabilities: capabilities(), can_launch_calls: false },
    sessions: { sessions: store.listSessions() },
    reps: { reps: repBreakdown(loadScoredSessions()) },
    personas: {
      personas: listPersonas().map((id) => {
        const p = loadPersona(id);
        return {
          id: p.id, name: p.name, role: p.role, version: p.version, difficulty: p.difficulty,
          headline: p.scenario?.headline, objections: p.objections?.length || 0,
          reveal_rules: p.reveal_rules?.length || 0,
        };
      }),
    },
    rubric: loadRubric(),
  };

  for (const session of store.listSessions()) {
    data[`sessions/${session.id}`] = {
      session,
      scorecard: store.readArtifact(session.id, 'scorecard.json'),
      transcript: null,
      has_report: store.hasArtifact(session.id, 'report.html'),
      has_recording: store.hasArtifact(session.id, 'recording.wav'),
      report_html: store.readArtifact(session.id, 'report.html'),
    };
  }
  return data;
}

export function buildSnapshot(outFile) {
  const css = fs.readFileSync(path.join(WEB, 'styles.css'), 'utf8');
  const js = fs.readFileSync(path.join(WEB, 'app.js'), 'utf8');
  const shell = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');

  const body = shell
    .replace(/[\s\S]*<body>/, '')
    .replace(/<\/body>[\s\S]*/, '')
    .replace(/<script[\s\S]*?<\/script>/g, '');

  // </script> inside the JSON would close the tag early.
  const payload = JSON.stringify(buildSnapshotData()).replace(/<\//g, '<\\/');

  const html = `<title>AI Sales Practice</title>
<style>${css}</style>
${body}
<script>window.__PRACTICE_SNAPSHOT__ = ${payload};</script>
<script type="module">${js}</script>`;

  fs.mkdirSync(path.dirname(outFile), { recursive: true });
  fs.writeFileSync(outFile, html);
  return { file: outFile, bytes: Buffer.byteLength(html), sessions: store.listSessions().length };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const out = process.argv[2] || path.join(ROOT, 'dist', 'dashboard.html');
  const result = buildSnapshot(out);
  console.log(`Wrote ${result.file}`);
  console.log(`  ${result.sessions} sessions, ${(result.bytes / 1024).toFixed(0)} KB, no server needed - open it in any browser.`);
}

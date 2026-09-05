import fs from 'node:fs';
import path from 'node:path';
import { config, ROOT, readJson } from './config.js';
import * as store from './store.js';
import { summarize, repBreakdown, loadScoredSessions } from './analytics.js';
import { listPersonas, loadPersona } from './persona.js';
import { loadRubric } from './score.js';
import { capabilities } from './config.js';

const WEB_DIR = path.join(ROOT, 'src', 'web');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.json': 'application/json',
};

function json(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(body));
}

/**
 * The dashboard is a shared password at most, not real auth - that is what
 * Supabase or Google sign-in is for later. What it does guarantee today is that
 * a stranger who finds the ngrok URL cannot spend the OpenAI budget: launching a
 * call is refused outright on a public URL with no token set.
 */
function tokenFrom(req, url) {
  const fromQuery = url.searchParams.get('token');
  if (fromQuery) return fromQuery;
  const cookie = req.headers.cookie || '';
  const match = cookie.match(/(?:^|;\s*)practice_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

export function checkDashboardAccess(req, url) {
  if (!config.dashboardToken) {
    return { allowed: true, canLaunchCalls: !config.publicUrl };
  }
  const allowed = tokenFrom(req, url) === config.dashboardToken;
  return { allowed, canLaunchCalls: allowed };
}

function readRequestJson(req) {
  return new Promise((resolve, reject) => {
    let raw = '';
    req.on('data', (c) => {
      raw += c;
      if (raw.length > 100_000) reject(new Error('Request body too large'));
    });
    req.on('end', () => {
      try { resolve(raw ? JSON.parse(raw) : {}); } catch { reject(new Error('Body is not valid JSON')); }
    });
  });
}

/** Serve the dashboard's static files. Returns true if it handled the request. */
export function serveStatic(req, res, url) {
  let pathname = url.pathname === '/' ? '/index.html' : url.pathname;
  const file = path.join(WEB_DIR, path.normalize(pathname).replace(/^(\.\.[/\\])+/, ''));

  if (!file.startsWith(WEB_DIR) || !fs.existsSync(file) || !fs.statSync(file).isFile()) return false;

  res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-cache' });
  res.end(fs.readFileSync(file));
  return true;
}

/** Returns true if it handled the request. */
export async function handleApi(req, res, url, { onLaunchCall }) {
  if (!url.pathname.startsWith('/api/')) return false;

  const access = checkDashboardAccess(req, url);
  if (!access.allowed) {
    json(res, 401, { error: 'This dashboard is password protected. Open it with ?token=...' });
    return true;
  }

  const [, , resource, id, sub] = url.pathname.split('/');

  if (req.method === 'GET' && resource === 'summary') {
    json(res, 200, { ...summarize(), capabilities: capabilities(), can_launch_calls: access.canLaunchCalls });
    return true;
  }

  if (req.method === 'GET' && resource === 'sessions' && !id) {
    json(res, 200, { sessions: store.listSessions() });
    return true;
  }

  if (req.method === 'GET' && resource === 'sessions' && id) {
    const session = (() => { try { return store.getSession(id); } catch { return null; } })();
    if (!session) { json(res, 404, { error: `No session "${id}"` }); return true; }
    json(res, 200, {
      session,
      scorecard: store.readArtifact(id, 'scorecard.json'),
      transcript: sub === 'full' ? store.readArtifact(id, 'transcript.json') : null,
      has_report: store.hasArtifact(id, 'report.html'),
      has_recording: store.hasArtifact(id, 'recording.wav'),
    });
    return true;
  }

  if (req.method === 'GET' && resource === 'reps') {
    json(res, 200, { reps: repBreakdown(loadScoredSessions()) });
    return true;
  }

  if (req.method === 'GET' && resource === 'personas') {
    json(res, 200, {
      personas: listPersonas().map((pid) => {
        const p = loadPersona(pid);
        return {
          id: p.id, name: p.name, role: p.role, version: p.version,
          difficulty: p.difficulty, headline: p.scenario?.headline,
          objections: p.objections?.length || 0, reveal_rules: p.reveal_rules?.length || 0,
        };
      }),
    });
    return true;
  }

  if (req.method === 'GET' && resource === 'rubric') {
    json(res, 200, loadRubric());
    return true;
  }

  if (req.method === 'POST' && resource === 'calls') {
    if (!access.canLaunchCalls) {
      json(res, 403, {
        error: 'Launching calls from a public URL requires DASHBOARD_TOKEN to be set. Set it in .env and reload with ?token=...',
      });
      return true;
    }
    if (!capabilities().live_calls) {
      json(res, 400, { error: 'Live calls are not configured yet. Run `npm run doctor` to see what is missing.' });
      return true;
    }
    try {
      const body = await readRequestJson(req);
      json(res, 200, await onLaunchCall(body));
    } catch (err) {
      json(res, 400, { error: err.message });
    }
    return true;
  }

  json(res, 404, { error: 'Unknown endpoint' });
  return true;
}

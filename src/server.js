import http from 'node:http';
import { WebSocketServer } from 'ws';
import { config, capabilities } from './config.js';
import { loadPersona } from './persona.js';
import { bridgeTwilioToRealtime } from './realtime-bridge.js';
import { streamTwiml, fetchRecording, verifySignature } from './twilio.js';
import * as store from './store.js';
import { transcribeAudio } from './transcribe.js';
import { scoreTranscript } from './score.js';
import { renderReport } from './report.js';
import { handleApi, serveStatic, checkDashboardAccess } from './web-routes.js';
import { launchCall } from './launch-call.js';

function readBody(req) {
  return new Promise((resolve) => {
    let raw = '';
    req.on('data', (c) => { raw += c; });
    req.on('end', () => {
      const params = {};
      for (const [k, v] of new URLSearchParams(raw)) params[k] = v;
      resolve({ raw, params });
    });
  });
}

function send(res, status, body, type = 'text/plain') {
  res.writeHead(status, { 'Content-Type': type });
  res.end(body);
}

function sessionOrNull(id) {
  try { return store.getSession(id); } catch { return null; }
}

/**
 * After the call ends we run the same pipeline the CLI runs, so a live call and
 * a replayed transcript produce identical artifacts.
 */
async function runPostCallPipeline(sessionId) {
  try {
    if (!store.hasArtifact(sessionId, 'transcript.json')) await transcribeAudio(sessionId);
    const session = sessionOrNull(sessionId);
    let persona = null;
    try { persona = session?.persona ? loadPersona(session.persona) : null; } catch {}

    const scorecard = await scoreTranscript(sessionId, { persona });
    renderReport(sessionId);
    store.updateSession(sessionId, { status: 'scored', score: scorecard.overall_score });
    console.log(`[pipeline] ${sessionId} scored ${scorecard.overall_score}/100`);
    if (config.publicUrl) console.log(`[pipeline] report: ${config.publicUrl}/report/${sessionId}`);
  } catch (err) {
    console.error(`[pipeline] ${sessionId} failed:`, err.message);
    store.updateSession(sessionId, { status: 'pipeline_error', error: err.message });
  }
}

async function handleRequest(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const [, route, sessionId] = url.pathname.split('/');

  // Dashboard first: its API, then its static files. Twilio's webhook routes are
  // matched below and never pass through the dashboard's access check.
  if (await handleApi(req, res, url, { onLaunchCall: launchCall })) return;

  if (url.pathname === '/health') {
    return send(res, 200, JSON.stringify({ ok: true, capabilities: capabilities() }), 'application/json');
  }

  if (url.pathname === '/sessions' && req.method === 'GET') {
    return send(res, 200, JSON.stringify(store.listSessions(), null, 2), 'application/json');
  }

  if (route === 'report' && req.method === 'GET' && sessionId) {
    if (!checkDashboardAccess(req, url).allowed) return send(res, 401, 'This dashboard is password protected');
    const html = store.readArtifact(sessionId, 'report.html');
    if (!html) return send(res, 404, 'No report for that session yet');
    return send(res, 200, html, 'text/html');
  }

  if (req.method === 'GET') {
    if (!checkDashboardAccess(req, url).allowed) return send(res, 401, 'This dashboard is password protected. Open it with ?token=...');
    if (serveStatic(req, res, url)) return;
    return send(res, 404, 'Not found');
  }

  if (req.method !== 'POST') return send(res, 404, 'Not found');
  if (!['voice', 'status', 'recording'].includes(route)) return send(res, 404, 'Not found');

  const { params } = await readBody(req);

  // Twilio signs every webhook. Verifying it means someone who guesses the URL
  // cannot drive calls through the bridge on our OpenAI key.
  const fullUrl = `${config.publicUrl}${url.pathname}`;
  if (config.twilio.authToken && !verifySignature({ signature: req.headers['x-twilio-signature'], url: fullUrl, params })) {
    console.warn(`[server] rejected unsigned ${route} webhook for ${sessionId}`);
    return send(res, 403, 'Invalid Twilio signature');
  }

  if (!sessionOrNull(sessionId)) {
    console.warn(`[server] ${route} webhook for unknown session "${sessionId}"`);
    return send(res, 404, 'Unknown session');
  }

  if (route === 'voice') {
    store.updateSession(sessionId, { status: 'answered', call_sid: params.CallSid || null });
    return send(res, 200, streamTwiml(sessionId), 'text/xml');
  }

  if (route === 'status') {
    const patch = { call_status: params.CallStatus, call_sid: params.CallSid || null };
    if (params.CallStatus === 'completed') {
      patch.status = 'call_completed';
      patch.duration_seconds = Number(params.CallDuration || 0);
    }
    store.updateSession(sessionId, patch);
    console.log(`[server] ${sessionId} call status: ${params.CallStatus}`);
    return send(res, 204, '');
  }

  // route === 'recording' - acknowledge immediately, then do the slow work.
  send(res, 204, '');
  try {
    const audio = await fetchRecording(params.RecordingUrl);
    store.saveArtifact(sessionId, 'recording.wav', audio);
    store.updateSession(sessionId, {
      status: 'recorded',
      recording_sid: params.RecordingSid,
      recording_seconds: Number(params.RecordingDuration || 0),
    });
    console.log(`[server] ${sessionId} recording saved (${params.RecordingDuration}s)`);
    await runPostCallPipeline(sessionId);
  } catch (err) {
    console.error(`[server] recording handler failed for ${sessionId}:`, err.message);
    store.updateSession(sessionId, { status: 'recording_error', error: err.message });
  }
}

export function createServer() {
  const server = http.createServer((req, res) => {
    // One boundary for the whole handler: a bad webhook must return a status
    // code, never take the server down in the middle of somebody's call.
    handleRequest(req, res).catch((err) => {
      console.error(`[server] ${req.method} ${req.url} failed:`, err.message);
      if (!res.headersSent) send(res, 500, 'Internal error');
    });
  });

  const wss = new WebSocketServer({ noServer: true });

  server.on('upgrade', (req, socket, head) => {
    const url = new URL(req.url, `http://${req.headers.host}`);
    const [, route, sessionId] = url.pathname.split('/');
    if (route !== 'media' || !sessionId) return socket.destroy();

    const session = sessionOrNull(sessionId);
    if (!session) {
      console.warn(`[server] media stream requested for unknown session "${sessionId}"`);
      return socket.destroy();
    }

    wss.handleUpgrade(req, socket, head, (ws) => {
      let persona;
      try {
        persona = loadPersona(session.persona || 'larry');
      } catch (err) {
        console.error(`[server] ${err.message}`);
        return ws.close();
      }

      console.log(`[server] media stream open for ${sessionId} (persona: ${persona.id})`);
      const liveTurns = [];

      bridgeTwilioToRealtime(ws, {
        persona,
        difficulty: session.difficulty,
        onEvent: (event) => {
          if (event.type === 'transcript') {
            liveTurns.push({ speaker: event.speaker, text: event.text });
            console.log(`  ${event.speaker}: ${event.text}`);
          }
          if (event.type === 'error') console.error('[realtime]', event.error?.message || event.error);
          if ((event.type === 'stream_stop' || event.type === 'twilio_close') && liveTurns.length) {
            store.saveArtifact(sessionId, 'live-transcript.json', { source: 'realtime', turns: liveTurns });
          }
        },
      });
    });
  });

  return server;
}

export function startServer(port = config.port) {
  const server = createServer();
  server.listen(port, () => {
    console.log(`AI Sales Practice server listening on :${port}`);
    console.log(`  dashboard:  http://localhost:${port}${config.dashboardToken ? '?token=...' : ''}`);
    console.log(`  public url: ${config.publicUrl || '(PUBLIC_URL not set - Twilio cannot reach this yet)'}`);
    for (const [name, ok] of Object.entries(capabilities())) console.log(`  ${ok ? '[ready]  ' : '[missing]'} ${name}`);
    if (config.publicUrl && !config.twilio.authToken) {
      console.warn('  WARNING: TWILIO_AUTH_TOKEN is unset, so webhook signatures are not being verified.');
    }
    if (config.publicUrl && !config.dashboardToken) {
      console.warn('  WARNING: PUBLIC_URL is set with no DASHBOARD_TOKEN - the dashboard is readable by anyone with the link,');
      console.warn('           and launching calls from it is disabled until you set one.');
    }
  });
  return server;
}

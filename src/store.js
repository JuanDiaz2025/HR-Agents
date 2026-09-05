import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { config, readJson, writeJson } from './config.js';

/**
 * A "session" is one practice call and everything derived from it:
 * metadata, the recording, the transcript, the scorecard and the report.
 * Everything lives on disk under data/sessions/<id>/ so nothing is lost
 * between CLI invocations and there is no database to stand up on day one.
 */

const sessionsDir = () => path.join(config.dataDir, 'sessions');

export function newSessionId(prefix = 'call') {
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..+/, '').replace('T', '-');
  return `${prefix}-${stamp}-${crypto.randomBytes(3).toString('hex')}`;
}

export function sessionDir(id) {
  return path.join(sessionsDir(), id);
}

export function createSession(fields = {}) {
  const id = fields.id || newSessionId();
  const session = {
    id,
    created_at: new Date().toISOString(),
    status: 'created',
    persona: fields.persona || null,
    rep: fields.rep || null,
    to: fields.to || null,
    from: fields.from || null,
    call_sid: fields.call_sid || null,
    ...fields,
  };
  writeJson(path.join(sessionDir(id), 'session.json'), session);
  return session;
}

export function getSession(id) {
  const file = path.join(sessionDir(id), 'session.json');
  if (!fs.existsSync(file)) throw new Error(`No session "${id}" under ${sessionsDir()}`);
  return readJson(file);
}

export function updateSession(id, patch) {
  const session = { ...getSession(id), ...patch, updated_at: new Date().toISOString() };
  writeJson(path.join(sessionDir(id), 'session.json'), session);
  return session;
}

export function listSessions() {
  if (!fs.existsSync(sessionsDir())) return [];
  return fs.readdirSync(sessionsDir())
    .filter((d) => fs.existsSync(path.join(sessionsDir(), d, 'session.json')))
    .map((d) => getSession(d))
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
}

/** Find a session by the Twilio CallSid the webhooks report back. */
export function findByCallSid(callSid) {
  return listSessions().find((s) => s.call_sid === callSid) || null;
}

export function saveArtifact(id, name, contents) {
  const file = path.join(sessionDir(id), name);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  if (typeof contents === 'string' || Buffer.isBuffer(contents)) fs.writeFileSync(file, contents);
  else writeJson(file, contents);
  return file;
}

export function readArtifact(id, name) {
  const file = path.join(sessionDir(id), name);
  if (!fs.existsSync(file)) return null;
  return name.endsWith('.json') ? readJson(file) : fs.readFileSync(file, 'utf8');
}

export function hasArtifact(id, name) {
  return fs.existsSync(path.join(sessionDir(id), name));
}

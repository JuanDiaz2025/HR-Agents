import { config, capabilities } from './config.js';
import * as store from './store.js';
import { loadPersona } from './persona.js';

const E164 = /^\+[1-9]\d{7,14}$/;

/**
 * Place one practice call. Shared by the CLI and the dashboard so both create
 * identical sessions - there is one code path that spends money, not two.
 */
export async function launchCall({ to, persona: personaId = 'larry', difficulty, rep = null } = {}) {
  if (!capabilities().live_calls) {
    throw new Error('Live calls are not configured. Run `npm run doctor` to see what is missing.');
  }

  const target = (to || config.salesLine || '').trim();
  if (!E164.test(target)) {
    throw new Error(`"${target}" is not a valid E.164 phone number (expected something like +14155550134)`);
  }

  const persona = loadPersona(personaId);
  const chosenDifficulty = difficulty || persona.difficulty || 'medium';
  if (!['easy', 'medium', 'hard'].includes(chosenDifficulty)) {
    throw new Error(`Unknown difficulty "${chosenDifficulty}" (expected easy, medium or hard)`);
  }

  const session = store.createSession({
    id: store.newSessionId('call'),
    persona: persona.id,
    difficulty: chosenDifficulty,
    rep: rep ? String(rep).slice(0, 80) : null,
    to: target,
    from: config.twilio.fromNumber,
    status: 'dialing',
  });

  const { placeCall } = await import('./twilio.js');
  try {
    const call = await placeCall({ to: target, from: config.twilio.fromNumber, sessionId: session.id });
    store.updateSession(session.id, { call_sid: call.sid, call_status: call.status });
    return { session_id: session.id, call_sid: call.sid, status: call.status, to: target, persona: persona.id, difficulty: chosenDifficulty };
  } catch (err) {
    store.updateSession(session.id, { status: 'dial_failed', error: err.message });
    throw err;
  }
}

import crypto from 'node:crypto';
import { config } from './config.js';

const API = 'https://api.twilio.com/2010-04-01';

function authHeader() {
  const { accountSid, authToken } = config.twilio;
  if (!accountSid || !authToken) throw new Error('TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN are not set (see .env.example)');
  return 'Basic ' + Buffer.from(`${accountSid}:${authToken}`).toString('base64');
}

async function twilioPost(pathname, params) {
  const res = await fetch(`${API}/Accounts/${config.twilio.accountSid}${pathname}`, {
    method: 'POST',
    headers: { Authorization: authHeader(), 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(params),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(`Twilio ${res.status}: ${body.message || JSON.stringify(body)}`);
  return body;
}

/**
 * Place the practice call: the AI seller dials your sales line.
 * Twilio fetches `url` when the rep answers, and we hand back TwiML that opens
 * the bidirectional media stream into the realtime bridge.
 */
export async function placeCall({ to, from, sessionId }) {
  if (!config.publicUrl) throw new Error('PUBLIC_URL is not set - Twilio needs a reachable https URL for the webhook');
  const call = await twilioPost('/Calls.json', {
    To: to,
    From: from,
    Url: `${config.publicUrl}/voice/${sessionId}`,
    Method: 'POST',
    StatusCallback: `${config.publicUrl}/status/${sessionId}`,
    StatusCallbackMethod: 'POST',
    StatusCallbackEvent: 'initiated ringing answered completed',
    Record: 'true',
    RecordingStatusCallback: `${config.publicUrl}/recording/${sessionId}`,
    RecordingStatusCallbackMethod: 'POST',
    RecordingChannels: 'dual',
    Timeout: '30',
  });
  return { sid: call.sid, status: call.status };
}

export async function fetchRecording(recordingUrl) {
  const url = recordingUrl.endsWith('.wav') ? recordingUrl : `${recordingUrl}.wav`;
  const res = await fetch(url, { headers: { Authorization: authHeader() } });
  if (!res.ok) throw new Error(`Twilio recording download ${res.status}`);
  return Buffer.from(await res.arrayBuffer());
}

/**
 * Twilio signs every webhook. Verifying it means a stranger who guesses the URL
 * cannot drive calls through the bridge on our OpenAI key.
 */
export function verifySignature({ signature, url, params }) {
  if (!config.twilio.authToken) return false;
  const data = url + Object.keys(params).sort().map((k) => k + params[k]).join('');
  const expected = crypto.createHmac('sha1', config.twilio.authToken).update(Buffer.from(data, 'utf8')).digest('base64');
  const a = Buffer.from(expected);
  const b = Buffer.from(signature || '');
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

/** TwiML that connects the answered call to our realtime bridge. */
export function streamTwiml(sessionId) {
  const wsUrl = config.publicUrl.replace(/^http/, 'ws') + `/media/${sessionId}`;
  return `<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="${wsUrl}" />
  </Connect>
</Response>`;
}

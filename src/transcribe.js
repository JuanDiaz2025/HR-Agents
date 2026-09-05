import fs from 'node:fs';
import path from 'node:path';
import { config } from './config.js';
import { splitStereoWav } from './wav.js';
import * as store from './store.js';

const OPENAI_TRANSCRIPTIONS = 'https://api.openai.com/v1/audio/transcriptions';

/** One raw transcription call. Returns { text, segments } with segment timings. */
export async function transcribeBuffer(buffer, filename, { language = 'en', prompt = '' } = {}) {
  if (!config.openai.apiKey) throw new Error('OPENAI_API_KEY is not set - cannot transcribe');

  const form = new FormData();
  form.append('file', new Blob([buffer]), filename);
  form.append('model', config.openai.transcribeModel);
  form.append('response_format', 'verbose_json');
  form.append('language', language);
  if (prompt) form.append('prompt', prompt);

  const res = await fetch(OPENAI_TRANSCRIPTIONS, {
    method: 'POST',
    headers: { Authorization: `Bearer ${config.openai.apiKey}` },
    body: form,
  });
  if (!res.ok) throw new Error(`Transcription failed ${res.status}: ${(await res.text()).slice(0, 300)}`);

  const body = await res.json();
  return {
    text: body.text || '',
    segments: (body.segments || []).map((s) => ({ start: s.start, end: s.end, text: (s.text || '').trim() })),
  };
}

/** Transcribe an arbitrary audio file, splitting speakers when the file is dual-channel. */
export async function transcribeFile(file, { channelSpeakers = ['left', 'right'], prompt = '' } = {}) {
  const buffer = fs.readFileSync(file);
  const name = path.basename(file);

  let split = null;
  if (name.toLowerCase().endsWith('.wav')) {
    try { split = splitStereoWav(buffer); } catch { split = null; }
  }

  if (!split) {
    const single = await transcribeBuffer(buffer, name, { prompt });
    return {
      source: name,
      diarized: false,
      turns: single.segments.map((s) => ({ speaker: 'unknown', text: s.text, start: s.start, end: s.end })),
      text: single.text,
    };
  }

  const [a, b] = await Promise.all([
    transcribeBuffer(split.left, name.replace(/\.wav$/i, '-ch1.wav'), { prompt }),
    transcribeBuffer(split.right, name.replace(/\.wav$/i, '-ch2.wav'), { prompt }),
  ]);

  const turns = [
    ...a.segments.map((s) => ({ speaker: channelSpeakers[0], text: s.text, start: s.start, end: s.end })),
    ...b.segments.map((s) => ({ speaker: channelSpeakers[1], text: s.text, start: s.start, end: s.end })),
  ].filter((t) => t.text).sort((x, y) => x.start - y.start);

  return { source: name, diarized: true, turns, text: turns.map((t) => `${t.speaker}: ${t.text}`).join('\n') };
}

/**
 * Produce transcript.json for a practice-call session.
 * Prefers the recording (verbatim, timestamped). Falls back to the transcript the
 * realtime bridge captured live, so a failed download never costs us a scorecard.
 */
export async function transcribeAudio(sessionId) {
  const session = store.getSession(sessionId);
  const recording = path.join(store.sessionDir(sessionId), 'recording.wav');

  if (fs.existsSync(recording) && config.openai.apiKey) {
    const result = await transcribeFile(recording, {
      // The AI seller placed the call, so it is the caller leg (channel 1).
      channelSpeakers: ['seller', 'rep'],
      prompt: 'A phone call between a home seller and a real estate acquisitions representative.',
    });
    const transcript = { session_id: sessionId, source: 'recording', ...result };
    store.saveArtifact(sessionId, 'transcript.json', transcript);
    store.saveArtifact(sessionId, 'transcript.txt', formatTranscript(transcript));
    store.updateSession(sessionId, { status: 'transcribed' });
    return transcript;
  }

  const live = store.readArtifact(sessionId, 'live-transcript.json');
  if (live?.turns?.length) {
    const transcript = { session_id: sessionId, source: 'realtime', diarized: true, turns: live.turns };
    store.saveArtifact(sessionId, 'transcript.json', transcript);
    store.saveArtifact(sessionId, 'transcript.txt', formatTranscript(transcript));
    store.updateSession(sessionId, { status: 'transcribed' });
    return transcript;
  }

  throw new Error(`No recording or live transcript for session ${sessionId} (status: ${session.status})`);
}

export function formatTranscript(transcript) {
  return transcript.turns
    .map((t) => {
      const stamp = typeof t.start === 'number' ? `[${String(Math.floor(t.start / 60)).padStart(2, '0')}:${String(Math.floor(t.start % 60)).padStart(2, '0')}] ` : '';
      const who = t.speaker === 'rep' ? 'REP' : t.speaker === 'seller' ? 'SELLER' : t.speaker.toUpperCase();
      return `${stamp}${who}: ${t.text}`;
    })
    .join('\n');
}

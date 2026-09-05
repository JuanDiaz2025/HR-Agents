import WebSocket from 'ws';
import { config } from './config.js';
import { buildInstructions } from './persona.js';

const REALTIME_URL = 'wss://api.openai.com/v1/realtime';

/**
 * Bridges one Twilio Media Stream to one OpenAI Realtime session.
 *
 * Both sides speak G.711 mu-law at 8 kHz, so audio frames pass straight through
 * in both directions without resampling. We also capture the live transcript as
 * a cheap backup - the recording is still transcribed properly afterwards, but
 * this means a scorecard is possible even if the recording fails to download.
 */
export function bridgeTwilioToRealtime(twilioWs, { persona, difficulty, onEvent = () => {} }) {
  const instructions = buildInstructions(persona, { difficulty });
  const turns = [];
  let streamSid = null;
  let callSid = null;
  let openaiReady = false;
  const pending = [];

  const openai = new WebSocket(`${REALTIME_URL}?model=${encodeURIComponent(config.openai.realtimeModel)}`, {
    headers: {
      Authorization: `Bearer ${config.openai.apiKey}`,
      'OpenAI-Beta': 'realtime=v1',
    },
  });

  const sendToOpenai = (payload) => {
    const message = JSON.stringify(payload);
    if (openaiReady && openai.readyState === WebSocket.OPEN) openai.send(message);
    else pending.push(message);
  };

  openai.on('open', () => {
    openai.send(JSON.stringify({
      type: 'session.update',
      session: {
        modalities: ['audio', 'text'],
        instructions,
        voice: persona.voice || 'ballad',
        input_audio_format: 'g711_ulaw',
        output_audio_format: 'g711_ulaw',
        input_audio_transcription: { model: 'whisper-1' },
        // Server-side VAD lets the seller interrupt and be interrupted, which is
        // the whole point of a practice call.
        turn_detection: { type: 'server_vad', threshold: 0.5, silence_duration_ms: 600, prefix_padding_ms: 300 },
        temperature: 0.8,
      },
    }));
    openaiReady = true;
    while (pending.length) openai.send(pending.shift());

    // The seller placed the call, so the seller speaks first.
    openai.send(JSON.stringify({
      type: 'response.create',
      response: { modalities: ['audio', 'text'], instructions: `Open the call now. Say: "${persona.opening_line}"` },
    }));
    onEvent({ type: 'realtime_open' });
  });

  openai.on('message', (raw) => {
    let event;
    try { event = JSON.parse(raw.toString()); } catch { return; }

    switch (event.type) {
      case 'response.audio.delta':
        if (streamSid && event.delta) {
          twilioWs.send(JSON.stringify({ event: 'media', streamSid, media: { payload: event.delta } }));
        }
        break;

      case 'input_audio_buffer.speech_started':
        // The rep started talking over the seller - stop the seller's audio the
        // way a real person would stop mid-sentence.
        if (streamSid) twilioWs.send(JSON.stringify({ event: 'clear', streamSid }));
        sendToOpenai({ type: 'response.cancel' });
        break;

      case 'conversation.item.input_audio_transcription.completed':
        if (event.transcript?.trim()) {
          turns.push({ speaker: 'rep', text: event.transcript.trim(), at: new Date().toISOString() });
          onEvent({ type: 'transcript', speaker: 'rep', text: event.transcript.trim() });
        }
        break;

      case 'response.audio_transcript.done':
        if (event.transcript?.trim()) {
          turns.push({ speaker: 'seller', text: event.transcript.trim(), at: new Date().toISOString() });
          onEvent({ type: 'transcript', speaker: 'seller', text: event.transcript.trim() });
        }
        break;

      case 'error':
        onEvent({ type: 'error', error: event.error });
        break;
    }
  });

  openai.on('error', (err) => onEvent({ type: 'error', error: { message: err.message } }));
  openai.on('close', () => onEvent({ type: 'realtime_close' }));

  twilioWs.on('message', (raw) => {
    let msg;
    try { msg = JSON.parse(raw.toString()); } catch { return; }

    switch (msg.event) {
      case 'start':
        streamSid = msg.start?.streamSid || null;
        callSid = msg.start?.callSid || null;
        onEvent({ type: 'stream_start', streamSid, callSid });
        break;
      case 'media':
        sendToOpenai({ type: 'input_audio_buffer.append', audio: msg.media.payload });
        break;
      case 'stop':
        onEvent({ type: 'stream_stop', streamSid, callSid, turns });
        if (openai.readyState === WebSocket.OPEN) openai.close();
        break;
    }
  });

  twilioWs.on('close', () => {
    if (openai.readyState === WebSocket.OPEN) openai.close();
    onEvent({ type: 'twilio_close', turns });
  });

  return { turns, close: () => { try { openai.close(); } catch {} try { twilioWs.close(); } catch {} } };
}

"""Agent webpage for `voice_output_mode=output_media`.

Recall's bot loads this page and streams its audio/video into the meeting, so
whatever this page says, the meeting hears. It holds a websocket back to us and
speaks each utterance we push, using the browser's own speech synthesis.

Compared with the Output Audio path this removes the server-side TTS round trip
and lets you render something on the bot's camera feed. The trade-off is a
browser in the loop, and voice quality limited to what the bot's Chrome has —
swap `speak()` for an ElevenLabs fetch + <audio> element for production voices.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect, status

from app.security import verify_realtime_token

log = logging.getLogger(__name__)

router = APIRouter(tags=["agent"])

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>AI Interviewer</title>
<style>
  html,body{margin:0;height:100%%;background:#0b1020;color:#e8ecf8;
    font:500 28px/1.4 system-ui,-apple-system,Segoe UI,sans-serif}
  .wrap{height:100%%;display:flex;flex-direction:column;align-items:center;
    justify-content:center;gap:24px;text-align:center;padding:48px}
  .dot{width:96px;height:96px;border-radius:50%%;background:#4f7cff;
    box-shadow:0 0 60px #4f7cff88;transition:transform .2s}
  .dot.speaking{animation:pulse 1.1s ease-in-out infinite}
  @keyframes pulse{0%%,100%%{transform:scale(1)}50%%{transform:scale(1.12)}}
  .caption{max-width:900px;font-size:32px}
  .meta{font-size:18px;opacity:.55}
</style></head>
<body><div class="wrap">
  <div class="dot" id="dot"></div>
  <div class="caption" id="caption">Connecting…</div>
  <div class="meta">AI screening interview · this call is recorded</div>
</div>
<script>
const INTERVIEW_ID = %(interview_id)r;
const TOKEN = %(token)r;
const dot = document.getElementById('dot');
const caption = document.getElementById('caption');
const queue = [];
let speaking = false;

function speak(text) {
  return new Promise((resolve) => {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.0;
    utterance.pitch = 1.0;
    utterance.onend = resolve;
    utterance.onerror = resolve;
    speechSynthesis.speak(utterance);
  });
}

async function drain() {
  if (speaking) return;
  speaking = true;
  while (queue.length) {
    const text = queue.shift();
    caption.textContent = text;
    dot.classList.add('speaking');
    await speak(text);
    dot.classList.remove('speaking');
  }
  speaking = false;
}

function connect() {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(
    `${scheme}://${location.host}/api/ws/agent/${INTERVIEW_ID}?token=${encodeURIComponent(TOKEN)}`
  );
  ws.onopen = () => { caption.textContent = 'Ready.'; };
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === 'speak' && msg.text) { queue.push(msg.text); drain(); }
  };
  ws.onclose = () => { caption.textContent = 'Interview ended.'; setTimeout(connect, 3000); };
  ws.onerror = () => ws.close();
}
connect();
</script></body></html>
"""


@router.get("/agent/{interview_id}")
def agent_page(interview_id: str, token: str | None = None) -> Response:
    if not verify_realtime_token(token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    html = PAGE % {"interview_id": interview_id, "token": token}
    return Response(content=html, media_type="text/html")


@router.websocket("/api/ws/agent/{interview_id}")
async def agent_socket(websocket: WebSocket, interview_id: str, token: str | None = None) -> None:
    if not verify_realtime_token(token):
        await websocket.close(code=4401, reason="invalid token")
        return
    rt = websocket.app.state.runtime
    if rt.browser_voice is None:
        await websocket.close(code=4400, reason="server is not in output_media mode")
        return

    await websocket.accept()
    queue = rt.browser_voice.queue_for(interview_id)
    log.info("[%s] agent page connected", interview_id[:8])
    try:
        while True:
            text = await queue.get()
            await websocket.send_json({"type": "speak", "text": text})
    except WebSocketDisconnect:
        log.info("[%s] agent page disconnected", interview_id[:8])
    except asyncio.CancelledError:
        raise

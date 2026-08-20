"""Inbound phone calls, bridged into the same agent the browser talks to.

Twilio Media Streams gives us 8kHz mu-law over a WebSocket. OpenAI Realtime
accepts mu-law natively (`audio/pcmu`), which is why there is no resampling
here. That convenience is OpenAI-specific: Gemini wants 16/24kHz PCM and Qwen
wants PCM16, so adding either provider means owning codec conversion.

Registered onto the same FastAPI app as the browser client so both share one
persona, one tool set, one port, and one Cloudflare tunnel. The alternative,
a second process on a second port, would have meant two personas drifting
apart and a second tunnel hostname to maintain.

Derived from twilio-samples/speech-assistant-openai-realtime-api-python
(MIT, (c) 2024 pkamp); the timing/truncation logic below is theirs.
"""
import base64
import json
import os

import websockets
from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

LOGGED = {
    "error", "response.done", "rate_limits.updated",
    "input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped",
    "session.created", "session.updated", "response.function_call_arguments.done",
}


def register(app, *, model, voice, instructions, schemas, tools, api_key, trace):
    """Attach /incoming-call and /media-stream to an existing app."""

    async def run_tool(openai_ws, event):
        name = event.get("name")
        args = {}
        try:
            args = json.loads(event.get("arguments") or "{}")
            fn = tools.get(name)
            result = fn(**args) if fn else {"error": f"unknown tool {name}"}
        except Exception as e:                      # noqa: BLE001
            result = {"error": str(e)}
        trace("tool", {"name": name, "args": args, "via": "phone",
                       "result": result})
        print(f"[phone tool] {name}({args})")
        await openai_ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {"type": "function_call_output",
                     "call_id": event.get("call_id"),
                     "output": json.dumps(result)}}))
        await openai_ws.send(json.dumps({"type": "response.create"}))

    @app.api_route("/incoming-call", methods=["GET", "POST"])
    async def incoming_call(request: Request):
        """TwiML that hands the call's audio to /media-stream."""
        host = request.headers.get("x-forwarded-host") or request.url.hostname
        trace("call_in", {"host": host})
        # Deliberately no <Say> preamble: the greeting is the model's job, and a
        # canned English line is wrong for a Vietnamese or Mandarin caller.
        xml = (f'<?xml version="1.0" encoding="UTF-8"?><Response>'
               f'<Connect><Stream url="wss://{host}/media-stream" /></Connect>'
               f'</Response>')
        return HTMLResponse(content=xml, media_type="application/xml")

    @app.websocket("/media-stream")
    async def media_stream(ws: WebSocket):
        await ws.accept()
        print("[phone] caller connected")

        async with websockets.connect(
            f"wss://api.openai.com/v1/realtime?model={model}",
            additional_headers={"Authorization": f"Bearer {api_key()}"},
        ) as oa:
            await oa.send(json.dumps({
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "model": model,
                    "output_modalities": ["audio"],
                    "audio": {
                        # 8kHz mu-law both directions: telephony's native codec
                        "input": {"format": {"type": "audio/pcmu"},
                                  "turn_detection": {"type": "server_vad"}},
                        "output": {"format": {"type": "audio/pcmu"},
                                   "voice": voice},
                    },
                    "instructions": instructions,
                    "tools": schemas,
                    "tool_choice": "auto",
                }}))
            # The caller dialled us, so the agent opens the conversation.
            await oa.send(json.dumps({"type": "response.create"}))

            stream_sid = None
            latest_ts = 0
            last_item = None
            marks = []
            started_at = None

            async def from_twilio():
                nonlocal stream_sid, latest_ts, last_item, started_at
                try:
                    async for msg in ws.iter_text():
                        d = json.loads(msg)
                        ev = d.get("event")
                        if ev == "media" and oa.state.name == "OPEN":
                            latest_ts = int(d["media"]["timestamp"])
                            await oa.send(json.dumps({
                                "type": "input_audio_buffer.append",
                                "audio": d["media"]["payload"]}))
                        elif ev == "start":
                            stream_sid = d["start"]["streamSid"]
                            started_at = None
                            latest_ts = 0
                            last_item = None
                            print(f"[phone] stream {stream_sid}")
                        elif ev == "mark" and marks:
                            marks.pop(0)
                except WebSocketDisconnect:
                    print("[phone] caller hung up")
                    if oa.state.name == "OPEN":
                        await oa.close()

            async def barge_in():
                """Caller started talking: cut our audio and tell the model how
                much of its reply actually reached them."""
                nonlocal started_at, last_item
                if marks and started_at is not None and last_item:
                    await oa.send(json.dumps({
                        "type": "conversation.item.truncate",
                        "item_id": last_item, "content_index": 0,
                        "audio_end_ms": latest_ts - started_at}))
                    await ws.send_json({"event": "clear", "streamSid": stream_sid})
                    marks.clear()
                    last_item = None
                    started_at = None

            async def to_twilio():
                nonlocal last_item, started_at
                try:
                    async for raw in oa:
                        r = json.loads(raw)
                        t = r.get("type")
                        if t in LOGGED:
                            print(f"[phone] {t}")

                        if t == "response.output_audio.delta" and "delta" in r:
                            await ws.send_json({
                                "event": "media", "streamSid": stream_sid,
                                "media": {"payload": r["delta"]}})
                            if r.get("item_id") and r["item_id"] != last_item:
                                started_at = latest_ts
                                last_item = r["item_id"]
                            if stream_sid:
                                marks.append(1)
                                await ws.send_json({
                                    "event": "mark", "streamSid": stream_sid,
                                    "mark": {"name": "part"}})

                        elif t == "response.function_call_arguments.done":
                            await run_tool(oa, r)

                        elif t == "input_audio_buffer.speech_started":
                            if last_item:
                                await barge_in()

                        elif t == "response.done":
                            u = (r.get("response") or {}).get("usage")
                            if u:
                                trace("phone_usage", {"usage": u})
                except Exception as e:                # noqa: BLE001
                    print(f"[phone] stream error: {e}")

            import asyncio
            await asyncio.gather(from_twilio(), to_twilio())

#!/usr/bin/env python3
"""Generate the short acknowledgment clips the client plays while you talk.

Backchannels ("mm-hm", "vâng", "嗯") cannot come from the Realtime API: its
turn-taking is explicitly designed to stop the model talking over you, and
there is no event for "emit a token of acknowledgement". So we synthesise a
handful of clips once, cache them as files, and play them locally. That costs
nothing per call, adds no latency, and cannot confuse the model's turn logic
because the audio never enters the microphone stream.

    op run --env-file=./.env.op -- ./.venv/bin/python make_backchannels.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "bc")

# Non-lexical sounds first: they read as listening rather than as agreeing,
# which matters when the speaker has not finished making their point.
CLIPS = {
    "en": ["Mm-hm.", "Mhm.", "Right.", "Yeah."],
    "vi": ["Vâng.", "Dạ.", "Ừ.", "Vâng, vâng."],
    "zh": ["嗯。", "嗯嗯。", "对。", "好。"],
}

# The acknowledgement must be the SAME voice as the realtime model, or it sounds
# like a second person in the room. `marin` matches REALTIME_VOICE; the rest are
# fallbacks in case a voice is not offered on the speech endpoint.
VOICES = [os.getenv("BC_VOICE", "marin"), "cedar", "sage"]

# Newest generation first, pinned to its latest snapshot. Verified against
# /v1/models: gpt-4o-mini-tts is the current TTS family (there is no non-mini
# variant); tts-1/tts-1-hd are the previous generation.
MODELS = ["gpt-4o-mini-tts-2025-12-15", "gpt-4o-mini-tts", "tts-1-hd"]


def speak(model, text, voice):
    body = json.dumps({
        "model": model, "voice": voice, "input": text,
        "response_format": "mp3", "speed": 1.05,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech", data=body, method="POST",
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main():
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY not set. Run under: op run --env-file=./.env.op --")
    os.makedirs(OUT, exist_ok=True)

    model = voice = None
    for m in MODELS:
        for v in VOICES:
            try:
                speak(m, "Mm-hm.", v)
                model, voice = m, v
                break
            except urllib.error.HTTPError as e:
                print(f"  {m} / {v}: {e.code}")
        if model:
            break
    if not model:
        sys.exit("no working model/voice pair; check /v1/models")
    print(f"using {model}, voice {voice}")

    manifest = {}
    for lang, phrases in CLIPS.items():
        names = []
        for i, text in enumerate(phrases):
            name = f"{lang}{i}.mp3"
            with open(os.path.join(OUT, name), "wb") as f:
                f.write(speak(model, text, voice))
            names.append(name)
            print(f"  {name:10} {text}")
        manifest[lang] = names

    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    total = sum(len(v) for v in manifest.values())
    print(f"{total} clips -> {OUT}")


if __name__ == "__main__":
    main()

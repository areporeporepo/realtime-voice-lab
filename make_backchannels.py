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

# Softer voices sound like a listener; a bright one sounds like an interruption.
VOICE = os.getenv("BC_VOICE", "sage")
MODELS = ["gpt-4o-mini-tts", "tts-1"]


def speak(model, text):
    body = json.dumps({
        "model": model, "voice": VOICE, "input": text,
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

    model = None
    for m in MODELS:
        try:
            speak(m, "Mm-hm.")
            model = m
            break
        except urllib.error.HTTPError as e:
            print(f"  {m}: {e.code}, trying next")
    if not model:
        sys.exit("no working TTS model; check the model names")
    print(f"using {model}, voice {VOICE}")

    manifest = {}
    for lang, phrases in CLIPS.items():
        names = []
        for i, text in enumerate(phrases):
            name = f"{lang}{i}.mp3"
            with open(os.path.join(OUT, name), "wb") as f:
                f.write(speak(model, text))
            names.append(name)
            print(f"  {name:10} {text}")
        manifest[lang] = names

    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    total = sum(len(v) for v in manifest.values())
    print(f"{total} clips -> {OUT}")


if __name__ == "__main__":
    main()

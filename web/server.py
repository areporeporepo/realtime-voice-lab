"""Server for the browser/iOS Form D voice client.

Two endpoints the client needs, and nothing else:

  POST /session   mint a short-lived OpenAI token so the client can talk
                  WebRTC directly to OpenAI without ever seeing our API key
  POST /api/tool  run a Form D query; SQL never leaves the server

The same two endpoints serve the Swift app unchanged.
"""
import json
import os
import sys
import time
import urllib.request

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from formd.tools import TOOLS, SCHEMAS  # noqa: E402

# The API key is injected by `op run` at launch (see run_web.sh). It is never
# read from a file, so there is no plaintext copy on disk to leak.

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.getenv("REALTIME_MODEL", "gpt-realtime-2.1")
VOICE = os.getenv("REALTIME_VOICE", "marin")
TRACE = os.path.join(HERE, "trace.jsonl")

# --- abuse / cost guard -----------------------------------------------------
# A public URL means anyone who finds it can spend our OpenAI credit. ACCESS_KEY
# gates who may mint a session; the caps bound the worst case even if it leaks.
ACCESS_KEY = os.getenv("ACCESS_KEY", "")
MAX_SESSIONS_PER_HOUR = int(os.getenv("MAX_SESSIONS_PER_HOUR", "30"))
MAX_TOOLS_PER_HOUR = int(os.getenv("MAX_TOOLS_PER_HOUR", "600"))
_hits = {"session": [], "tool": []}


def _rate_limit(bucket, ceiling):
    """Rolling one-hour counter. Raises 429 when the ceiling is reached."""
    now = time.time()
    stamps = _hits[bucket]
    stamps[:] = [t for t in stamps if now - t < 3600]
    if len(stamps) >= ceiling:
        _trace("rate_limited", {"bucket": bucket, "ceiling": ceiling})
        raise HTTPException(
            429, f"{bucket} limit reached ({ceiling}/hour). Try again later.")
    stamps.append(now)

# The industry column is free text from the filings, so the model has to map
# whatever the caller says (in any language) onto one of these exact strings.
# Listing them is far more reliable than hoping a translation happens to match.
INDUSTRIES = (
    "Other, Other Technology, Other Real Estate, Commercial, Residential, "
    "Pooled Investment Fund, Other Health Care, REITS and Finance, "
    "Biotechnology, Investing, Other Banking and Financial Services, "
    "Oil and Gas, Manufacturing, Business Services, Insurance, Other Energy, "
    "Computers, Retailing, Restaurants, Commercial Banking, Pharmaceuticals, "
    "Agriculture, Construction, Telecommunications, Investment Banking, "
    "Hospitals and Physicians"
)

INSTRUCTIONS = (
    "You are a private-markets research analyst speaking out loud. You have "
    "live access to 32,374 SEC Form D private-offering filings from January "
    "through June 2026, covering 53,574 named executives and directors.\n\n"

    "LANGUAGE\n"
    "- You speak English, Mandarin Chinese, and Vietnamese. Reply in whichever "
    "of those three the caller is using, and switch if they switch mid-call.\n"
    "- If the caller uses a fourth language, say briefly in that language (or in "
    "English if you cannot) that you handle English, Mandarin, and Vietnamese.\n"
    "- Speak amounts naturally in the caller's own language, never as digit "
    "strings: 'sixteen point six billion dollars', '一百六十六亿美元', "
    "'mười sáu phẩy sáu tỷ đô la'.\n"
    "- Never translate a company or person's name. They are legal names as "
    "filed: say 'X.AI Holdings Corp.' unchanged in every language.\n\n"

    "TOOL ARGUMENTS ARE ALWAYS ENGLISH\n"
    "- The database is English only. No matter what language the caller uses, "
    "tool arguments must be English.\n"
    "- 'state' is a two-letter US code: California / 加州 / California -> CA.\n"
    "- 'industry' must match one of these exact strings: " + INDUSTRIES + ".\n"
    "- So 科技 or 'công nghệ' -> industry='Technology' (it substring-matches "
    "'Other Technology'); 生物科技 or 'công nghệ sinh học' -> 'Biotechnology'; "
    "房地產 or 'bất động sản' -> 'Real Estate'.\n\n"

    "HOW TO ANSWER\n"
    "- One or two sentences. Never list more than three companies unless asked.\n"
    "- Call a tool before stating ANY number. Never recall a figure from memory. "
    "If a tool returns nothing, say so plainly rather than guessing.\n"
    "- Pooled investment funds are excluded by default, because 21,047 of the "
    "32,374 filings are PE and VC fund vehicles rather than operating companies. "
    "Mention the exclusion only if asked about funds.\n"
    "- Data ends June 30, 2026. Say so if asked about later dates.\n"
    "- If a question is ambiguous, ask one short clarifying question."
)

app = FastAPI()

# The page may be served from GitHub Pages while this backend runs elsewhere, so
# it needs to be a permitted cross-origin caller. ALLOWED_ORIGINS is a
# comma-separated list; the access key, not CORS, is what actually gates spend.
_default_origins = ",".join([
    "https://areporeporepo.github.io",
    "http://localhost:5050",
    "http://127.0.0.1:5050",
])
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", _default_origins).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


def _api_key():
    key = os.getenv("OPENAI_API_KEY")
    if not key or key.startswith("PASTE"):
        raise HTTPException(500, "OPENAI_API_KEY is not set on the server")
    return key


def _trace(kind, payload):
    """Append one event to the trace log. This is the cost/protocol dataset.

    Best-effort: on a container the filesystem is ephemeral and may be
    read-only, and losing an analytics line must never fail a live call."""
    try:
        with open(TRACE, "a") as f:
            f.write(json.dumps({"t": time.time(), "kind": kind, **payload}) + "\n")
    except Exception:                                       # noqa: BLE001
        pass


@app.get("/healthz")
def healthz():
    """Liveness for the platform health check. Confirms the database is really
    readable, not just that the process is up: a served page with a broken DB
    is worse than a machine that fails its check and gets replaced."""
    try:
        n = TOOLS["market_stats"]()["filings"]
    except Exception as e:                                  # noqa: BLE001
        raise HTTPException(503, f"database unavailable: {e}")
    return JSONResponse({"ok": True, "filings_visible": n, "model": MODEL})


@app.get("/")
def index():
    return FileResponse(os.path.join(ROOT, "docs", "index.html"))


# Cached backchannel clips ("mm-hm", "vâng", "嗯"). Static, no key required:
# they are three-word audio files, and gating them would only break playback.
app.mount("/bc", StaticFiles(directory=os.path.join(ROOT, "docs", "bc")),
          name="bc")


@app.post("/session")
def session(k: str = ""):
    """Mint an ephemeral client token with our persona and tools baked in."""
    if ACCESS_KEY and k != ACCESS_KEY:
        _trace("denied", {"reason": "bad access key"})
        raise HTTPException(403, "Access key required. Append ?k=<key> to the URL.")
    _rate_limit("session", MAX_SESSIONS_PER_HOUR)

    body = json.dumps({
        "session": {
            "type": "realtime",
            "model": MODEL,
            "instructions": INSTRUCTIONS,
            "audio": {"output": {"voice": VOICE}},
            "tools": SCHEMAS,
            "tool_choice": "auto",
        }
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/realtime/client_secrets",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {_api_key()}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        _trace("session_error", {"status": e.code, "detail": detail})
        raise HTTPException(e.code, f"OpenAI rejected the session: {detail}")

    _trace("session", {"model": MODEL, "voice": VOICE})
    # Token lives at .value on the current API; older shapes nested it.
    token = data.get("value") or (data.get("client_secret") or {}).get("value")
    return JSONResponse({"token": token, "model": MODEL, "raw": data})


@app.post("/api/tool")
async def api_tool(request: Request):
    """Execute one Form D tool call relayed by the client."""
    _rate_limit("tool", MAX_TOOLS_PER_HOUR)
    body = await request.json()
    name = body.get("name")
    args = body.get("arguments") or {}
    if isinstance(args, str):
        args = json.loads(args or "{}")

    fn = TOOLS.get(name)
    t0 = time.perf_counter()
    if fn is None:
        result = {"error": f"unknown tool {name}"}
    else:
        try:
            result = fn(**args)
        except Exception as e:
            result = {"error": str(e)}
    ms = round((time.perf_counter() - t0) * 1000, 1)

    _trace("tool", {"name": name, "args": args, "ms": ms, "result": result})
    print(f"[tool] {name}({args}) -> {ms}ms")
    return JSONResponse({"result": result, "ms": ms})


@app.post("/api/trace")
async def api_trace(request: Request):
    """Client-side timing and usage events, for the cost benchmark."""
    _trace("client", await request.json())
    return JSONResponse({"ok": True})


# Inbound phone calls share this process, so they get the same persona, the same
# tools, and the same tunnel. See web/phone.py for why the seam is here.
from web import phone  # noqa: E402

phone.register(app, model=MODEL, voice=VOICE, instructions=INSTRUCTIONS,
               schemas=SCHEMAS, tools=TOOLS, api_key=_api_key, trace=_trace)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 5050)))

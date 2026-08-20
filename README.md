# realtime-voice-lab

Continuous, interruptible voice conversation with a database. You talk, it queries
**32,374 real SEC Form D private-offering filings**, and it answers out loud.

Built on the OpenAI Realtime API over WebRTC, so it behaves like the ChatGPT app's
Advanced Voice mode rather than a record-and-wait voice-note bot: full duplex, you
can talk over it and it stops.

```
you: what were the three biggest technology raises in California this year?
     -> top_raises(state=CA, industry=Technology, limit=3)   30ms
ai:  X.AI Holdings at sixteen point six billion, Baseten at one point one
     billion, and Superhuman at one billion.
```

## Why this exists

Two questions, one codebase.

1. **Can a voice model be trusted with facts?** The model must call a tool before
   stating any number, and every answer has a checkable SQL ground truth. That
   makes hallucination measurable instead of anecdotal.
2. **What does continuous voice actually cost per minute?** Published price cards
   are quoted per million audio tokens, and in a realtime session prior audio is
   re-billed as input on every turn. Real cost on short calls diverges sharply
   from the naive estimate. The client records per-turn token usage so the gap can
   be measured rather than guessed.

Planned: the same workload against Gemini Live and `qwen3.5-omni-plus-realtime`.
Notably, **no LiveKit or Pipecat plugin exists for Qwen's native speech-to-speech
model** (`livekit-plugins-aliyun` and Pipecat's `QwenLLMService` only wrap
cascaded STT/TTS/text-LLM pieces), so that adapter is an open gap.

## Layout

```
formd/build_db.py     Form D JSONL -> SQLite (filings + normalized persons)
formd/tools.py        4 query tools and their realtime tool schemas
web/server.py         mints ephemeral tokens; runs tool calls; SQL stays here
web/index.html        WebRTC client with live lag / token / cost readout
twilio-base/main.py   inbound phone-call path (derived, see its NOTICE.md)
save_key.sh           put an API key in 1Password without exposing it
run_web.sh            launch the browser/iOS server
run.sh                launch the phone webhook server
```

## Architecture

The client speaks WebRTC **directly** to OpenAI, so audio never round-trips
through this server and latency stays low. Only two things come from the server:

```
POST /session    ephemeral token from /v1/realtime/client_secrets,
                 with persona and tool schemas already attached
POST /api/tool   runs one Form D query and returns a speakable result
```

When the model fires a tool call over the WebRTC data channel, the client relays
it to `/api/tool`. **The API key and the SQL both stay server-side.** The same two
endpoints serve a native iOS client unchanged, which is the point: the browser
client is the reference implementation, not a throwaway.

## Data

32,374 Form D filings, 2026-01-02 through 2026-06-30. 108,402 person rows
covering 53,574 distinct executives, directors, and promoters. Form D is the
notice a company files with the SEC when it raises money in an exempt private
offering, so this is a census of private fundraising rather than a sample.

Pooled investment funds are excluded from queries by default, because 21,047 of
the 32,374 filings are PE and VC fund vehicles rather than operating companies.
Leaving them in makes every "biggest raise" answer a list of fund closes.

The build script expects `~/vcwatch/data/formd.jsonl`, which is not in this repo.
It is derived from public SEC EDGAR Form D filings. Anyone reproducing this needs
to fetch and shape their own copy; the expected record shape is visible in
`formd/build_db.py`.

## Tools

| Tool | Answers |
|---|---|
| `search_filings` | "Any biotech filings in California this month?" |
| `top_raises` | "What were the biggest tech raises this year?" |
| `find_person` | "What offerings is Jane Smith named on?" |
| `market_stats` | "How much was raised in California since June?" |

Every dollar amount leaves the database layer as a **pre-formatted string**
(`"$16.6 billion"`), never a raw number. The model can read it back but cannot do
arithmetic on it or invent one, which is the first layer of the hallucination
guard.

## Secrets

The API key lives only in 1Password. There is no plaintext copy on disk.

```bash
cp .env.op.example .env.op     # holds op:// references, never secrets
./save_key.sh                  # optional: store a key via a hidden prompt
```

`run_web.sh` resolves the key three ways, in order: an existing `OPENAI_API_KEY`
in the environment, then `op run --env-file=./.env.op`, then a clear error. `op run`
injects the value into the subprocess environment only, and masks it in output.

## Run

```bash
uv venv .venv
uv pip install --python ./.venv/bin/python fastapi uvicorn websockets twilio
python3 formd/build_db.py
./run_web.sh
```

Open <http://localhost:5050>. `localhost` counts as a secure origin, so the
microphone works without HTTPS.

**On a phone**, expose it over HTTPS (no account or domain needed):

```bash
cloudflared tunnel --url http://localhost:5050
```

Open the printed `trycloudflare.com` URL in **iOS Safari as a normal tab**. Do not
use "Add to Home Screen": standalone-mode PWAs hit
[WebKit bug 185448](https://bugs.webkit.org/show_bug.cgi?id=185448) and lose
microphone access.

## Inbound phone calls

`twilio-base/` bridges Twilio Media Streams to the Realtime API for callers
dialing a real number. Point a Twilio number's "A call comes in" webhook at
`https://<tunnel>/incoming-call` and run `./run.sh`. This path is derived from a
Twilio sample; see `twilio-base/NOTICE.md`.

## Status

Verified: database build, all four tools against real data, `/session` minting a
token with all tools registered on `gpt-realtime-2.1`, `/api/tool` at ~30ms,
server boot under `op run`.

Not yet built: the `ask_analyst` escalation path for questions the four tools
cannot express, the per-turn fact ledger that checks spoken numbers against what
tools actually returned, a text-mode evaluation suite (realtime APIs accept text
input, so conversations can be tested at a fraction of audio cost), and the Gemini
and Qwen adapters.

## License

MIT. See `LICENSE`, and `twilio-base/NOTICE.md` for third-party attribution.

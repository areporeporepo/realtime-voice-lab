#!/usr/bin/env bash
# Serve the browser/iOS Form D voice client on :5050.
#
# Key resolution, in order:
#   1. OPENAI_API_KEY already in the environment  -> use it (fastest, no 1Password)
#   2. .env.op + an active `op` session           -> resolved by `op run`
#   3. neither                                    -> explain and exit
#
# In every case the key stays in process memory. Nothing is written to disk.
set -euo pipefail
cd "$(dirname "$0")"

PY=./.venv/bin/python
[ -x "$PY" ] || { echo "Missing venv. Run: uv venv .venv" >&2; exit 1; }

# --- 1. already in the environment -----------------------------------------
if [ -n "${OPENAI_API_KEY:-}" ]; then
  echo "Using OPENAI_API_KEY from the environment."
  echo "Serving on http://localhost:5050"
  exec "$PY" web/server.py
fi

# --- 2. via 1Password -------------------------------------------------------
# Do NOT gate this on `op whoami`. With desktop-app integration, whoami itself
# needs app authorization and fails spuriously whenever the app has re-locked.
# Just run `op run` and let it raise its own Touch ID prompt.
if [ -f .env.op ] && command -v op >/dev/null 2>&1; then
  echo "Resolving the key from 1Password (approve the Touch ID prompt if it appears)."
  echo "Serving on http://localhost:5050"
  exec op run --env-file=./.env.op -- "$PY" web/server.py
fi

# --- 3. neither -------------------------------------------------------------
cat >&2 <<'MSG'
No key available.

Fastest option, key never touches disk or shell history:

    read -rs OPENAI_API_KEY && export OPENAI_API_KEY && ./run_web.sh

(paste the key at the silent prompt, press Enter)

Or, to use 1Password: enable "Integrate with 1Password CLI" in
1Password > Settings > Developer, then run ./run_web.sh again.
MSG
exit 1

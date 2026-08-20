#!/usr/bin/env bash
# Start the Twilio phone-call webhook server (inbound calls) on :5050.
# Key comes from 1Password at launch, same as run_web.sh.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env.op ]; then
  echo "Missing .env.op. Run ./save_key.sh first." >&2
  exit 1
fi

if ! op whoami --account aircloudy >/dev/null 2>&1; then
  echo "1Password session expired. Signing in..."
  eval "$(op signin --account aircloudy)"
fi

cd twilio-base
exec op run --env-file=../.env.op -- ../.venv/bin/python main.py

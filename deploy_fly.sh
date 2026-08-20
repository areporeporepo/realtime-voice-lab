#!/usr/bin/env bash
# Deploy to Fly, pulling secrets straight from 1Password so neither the API key
# nor the access passphrase ever appears in argv, shell history, or a file.
#
#   fly auth login        # once, needs a browser
#   ./deploy_fly.sh
set -euo pipefail
cd "$(dirname "$0")"

command -v fly >/dev/null || { echo "flyctl not found" >&2; exit 1; }
fly auth whoami >/dev/null 2>&1 || { echo "Run: fly auth login" >&2; exit 1; }
[ -f formd/formd.db ] || { echo "Run: python3 formd/build_db.py first" >&2; exit 1; }

APP=$(awk -F'"' '/^app *=/{print $2}' fly.toml)
echo "app: $APP"

fly apps list 2>/dev/null | grep -q "^$APP" || fly apps create "$APP" --org personal

# `fly secrets import` reads KEY=VALUE lines from stdin, which keeps the values
# out of the process table. --stage defers the restart until deploy.
{
  printf 'OPENAI_API_KEY=%s\n' "$(op read 'op://Employee/openai/password')"
  printf 'ACCESS_KEY=%s\n'     "$(op read 'op://Employee/formd-voice access/password')"
} | fly secrets import --app "$APP" --stage

fly deploy --app "$APP" --ha=false

echo
echo "Live at https://$APP.fly.dev"
echo "Health  https://$APP.fly.dev/healthz"
echo
echo "Then repoint voice.hienhoa.com at Fly instead of the laptop tunnel:"
echo "  fly certs add voice.hienhoa.com --app $APP"
echo "  (and update the CNAME to $APP.fly.dev, proxied off)"

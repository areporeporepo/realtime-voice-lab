#!/usr/bin/env bash
# Create the Modal secret for the deployed voice agent.
#
# Both values are written to a mode-600 temp file and handed to Modal via
# --from-json, so neither ever appears in argv (visible to other processes) or in
# shell history. The generated access passphrase is stored in 1Password; this
# script never prints either value.
set -euo pipefail
cd "$(dirname "$0")"

SECRET_NAME="formd-voice"
KEY_REF="op://Employee/openai/password"
ACCESS_ITEM="formd-voice access"
VAULT="Employee"

command -v modal >/dev/null || { echo "modal CLI not found" >&2; exit 1; }
command -v op    >/dev/null || { echo "op CLI not found" >&2; exit 1; }

# --- pull the OpenAI key from 1Password ------------------------------------
echo "Reading the OpenAI key from 1Password (approve Touch ID if prompted)..."
API_KEY="$(op read "$KEY_REF")"
[ -n "$API_KEY" ] || { echo "could not resolve $KEY_REF" >&2; exit 1; }
case "$API_KEY" in sk-*) ;; *) echo "resolved value is not an sk- key" >&2; exit 1 ;; esac
echo "  got a ${#API_KEY}-character key."

# --- generate the access passphrase ----------------------------------------
# Reuse the existing one if we already stored it, so redeploying does not
# silently invalidate a URL you already shared.
if ACCESS_KEY="$(op read "op://$VAULT/$ACCESS_ITEM/password" 2>/dev/null)" \
   && [ -n "$ACCESS_KEY" ]; then
  echo "  reusing the existing access passphrase from 1Password."
  NEW_ACCESS=0
else
  ACCESS_KEY="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)"
  NEW_ACCESS=1
fi

# --- hand both to Modal via a mode-600 temp file --------------------------
TMP="$(mktemp)"
chmod 600 "$TMP"
trap 'rm -f "$TMP"' EXIT

API_KEY="$API_KEY" ACCESS_KEY="$ACCESS_KEY" python3 - > "$TMP" <<'PY'
import json, os
print(json.dumps({
    "OPENAI_API_KEY": os.environ["API_KEY"],
    "ACCESS_KEY": os.environ["ACCESS_KEY"],
    "REALTIME_MODEL": "gpt-realtime-2.1",
    "REALTIME_VOICE": "marin",
    "MAX_SESSIONS_PER_HOUR": "30",
    "MAX_TOOLS_PER_HOUR": "600",
}))
PY

modal secret create "$SECRET_NAME" --from-json "$TMP" --force >/dev/null
rm -f "$TMP"
echo "Modal secret '$SECRET_NAME' created."

# --- store the passphrase in 1Password if it is new -----------------------
if [ "$NEW_ACCESS" = "1" ]; then
  # Start from 1Password's own Password template. Hand-built JSON fails
  # validation ("Password item requires ps value") and specifying the category
  # in both the template and --category is also rejected.
  INJ="$(mktemp)"; trap 'rm -f "$TMP" "$INJ"' EXIT
  cat > "$INJ" <<'PY'
import json, os, sys
tpl = json.load(sys.stdin)
tpl["title"] = os.environ["ITEM"]
for f in tpl.get("fields", []):
    if f.get("id") == "password":
        f["value"] = os.environ["ACCESS_KEY"]
        break
else:
    tpl.setdefault("fields", []).append(
        {"id": "password", "label": "password",
         "type": "CONCEALED", "value": os.environ["ACCESS_KEY"]})
json.dump(tpl, sys.stdout)
PY
  # A `VAR=x a | b | c` prefix only reaches `a`, so export for the pipeline.
  export ACCESS_KEY
  export ITEM="$ACCESS_ITEM"
  op item template get Password \
    | python3 "$INJ" \
    | op item create --vault "$VAULT" - >/dev/null
  unset ITEM
  echo "Access passphrase stored at op://$VAULT/$ACCESS_ITEM/password"
fi

unset API_KEY ACCESS_KEY
cat <<EOF

Next:
  python3 formd/build_db.py       # if formd/formd.db is missing
  modal deploy deploy_modal.py

Then open the printed URL with ?k=<passphrase> appended. Get the passphrase with:
  op read "op://$VAULT/$ACCESS_ITEM/password"
EOF

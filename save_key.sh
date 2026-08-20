#!/usr/bin/env bash
# Put the OpenAI API key into 1Password, or inspect what's already there.
#
# The key is read from a hidden prompt and handed to `op` over a pipe, so it
# never appears in argv (visible to other processes per 1Password's own
# warning), in shell history, or in any agent transcript.
#
#   ./save_key.sh --check          inspect only, change nothing
#   ./save_key.sh                  store or update, Employee vault
#   ./save_key.sh --vault Private  use a different vault
#   ./save_key.sh --force          accept a key that isn't sk-*
set -euo pipefail

VAULT="Employee"
TITLE="OpenAI API"
ACCOUNT="aircloudy"
CHECK=0
FORCE=0
HERE="$(cd "$(dirname "$0")" && pwd)"

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --force) FORCE=1 ;;
    --vault) VAULT="$2"; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# --- sign in (interactive; no biometric integration on this machine) ---------
if ! op whoami --account "$ACCOUNT" >/dev/null 2>&1; then
  echo "Signing in to 1Password ($ACCOUNT)..."
  eval "$(op signin --account "$ACCOUNT")"
fi
op whoami --account "$ACCOUNT" >/dev/null || { echo "sign-in failed" >&2; exit 1; }

# --- diagnose: how many items match, and what does each actually hold? -------
echo
echo "Items titled '$TITLE' across all vaults:"
MATCHES=$(op item list --format=json 2>/dev/null \
  | python3 -c '
import json, sys
want = sys.argv[1].lower()
rows = [i for i in json.load(sys.stdin) if i.get("title","").lower() == want]
for i in rows:
    print(f'"'"'{i["id"]}\t{i["vault"]["name"]}\t{i.get("updated_at","")[:19]}'"'"')
' "$TITLE" || true)

if [ -z "$MATCHES" ]; then
  echo "  (none)"
else
  printf '%s\n' "$MATCHES" | while IFS=$'\t' read -r id vault updated; do
    val=$(op item get "$id" --fields label=credential --reveal 2>/dev/null || true)
    n=${#val}
    prefix=$(printf '%s' "$val" | cut -c1-7)
    verdict="LOOKS WRONG"
    case "$val" in sk-*) [ "$n" -ge 40 ] && verdict="looks valid" ;; esac
    echo "  vault=$vault  updated=$updated  length=$n  starts='$prefix...'  -> $verdict"
    echo "     id=$id"
  done
fi
echo

DUPES=$(printf '%s' "$MATCHES" | grep -c . || true)
if [ "$DUPES" -gt 1 ]; then
  echo "WARNING: $DUPES items share this title. op:// references by title are"
  echo "ambiguous. Delete the stale one(s) in the 1Password app, or reference"
  echo "the item by id instead of title."
  echo
fi

if [ "$CHECK" = "1" ]; then
  echo "Check-only mode, nothing changed."
  exit 0
fi

# --- hidden prompt ----------------------------------------------------------
printf 'Paste the OpenAI API key (input is hidden, nothing will echo): '
IFS= read -rs SECRET_VALUE
printf '\n'
[ -n "$SECRET_VALUE" ] || { echo "nothing entered, aborting" >&2; exit 1; }

# Validate hard. A bad value silently stored is exactly the failure we just hit.
BAD=""
case "$SECRET_VALUE" in sk-*) ;; *) BAD="does not start with 'sk-'" ;; esac
[ ${#SECRET_VALUE} -ge 40 ] || BAD="${BAD:+$BAD; }only ${#SECRET_VALUE} characters (expected 40+)"
if [ -n "$BAD" ]; then
  echo "REJECTED: $BAD" >&2
  echo "Length seen: ${#SECRET_VALUE}. Nothing was written." >&2
  [ "$FORCE" = "1" ] || { echo "Re-run with --force to store it anyway." >&2; exit 1; }
  echo "--force given, storing anyway." >&2
fi
export SECRET_VALUE

# --- write: edit the existing item if there is exactly one, else create ------
INJECT="$(mktemp)"
trap 'rm -f "$INJECT"; unset SECRET_VALUE 2>/dev/null || true' EXIT
cat > "$INJECT" <<'PY'
import json, os, sys

item = json.load(sys.stdin)
item["title"] = os.environ["ITEM_TITLE"]
secret = os.environ["SECRET_VALUE"]

placed = False
for f in item.get("fields", []):
    if f.get("id") == "credential" or f.get("label", "").lower() == "credential":
        f["value"] = secret
        f["type"] = "CONCEALED"
        placed = True
if not placed:
    item.setdefault("fields", []).append(
        {"id": "credential", "label": "credential",
         "type": "CONCEALED", "value": secret})

if not item.get("urls"):
    item["urls"] = [{"primary": True, "href": "https://platform.openai.com"}]
json.dump(item, sys.stdout)
PY

EXISTING_ID=$(printf '%s' "$MATCHES" | awk -F'\t' -v v="$VAULT" '$2==v {print $1; exit}')

if [ -n "$EXISTING_ID" ]; then
  echo "Updating existing item in '$VAULT' (id=$EXISTING_ID)..."
  ITEM_TITLE="$TITLE" \
    op item get "$EXISTING_ID" --format=json \
    | python3 "$INJECT" \
    | op item edit "$EXISTING_ID" >/dev/null
else
  echo "Creating a new item in '$VAULT'..."
  ITEM_TITLE="$TITLE" \
    op item template get "API Credential" \
    | python3 "$INJECT" \
    | op item create --vault "$VAULT" - >/dev/null
fi
unset SECRET_VALUE

# --- reference file (no secret in it, safe to commit) -----------------------
cat > "$HERE/.env.op" <<EOF
# Secret *references*, not secrets. Resolved at launch by \`op run\`.
# Nothing in this file is sensitive, which is why it is committed.
OPENAI_API_KEY=op://$VAULT/$TITLE/credential
REALTIME_MODEL=gpt-realtime-2.1
REALTIME_VOICE=marin
PORT=5050
EOF

# --- prove it, without revealing the value ---------------------------------
READBACK=$(op read "op://$VAULT/$TITLE/credential" --account "$ACCOUNT")
echo
echo "Read back op://$VAULT/$TITLE/credential"
echo "  length : ${#READBACK}"
echo "  starts : $(printf '%s' "$READBACK" | cut -c1-7)..."
case "$READBACK" in
  sk-*) echo "  status : looks like a real OpenAI key" ;;
  *)    echo "  status : STILL WRONG, the stored value does not start with sk-" ;;
esac
echo
echo "Next:  ./run_web.sh   then open http://localhost:5050"

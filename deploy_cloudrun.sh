#!/usr/bin/env bash
# Deploy to Google Cloud Run. Same Dockerfile as local; secrets come from
# 1Password into Secret Manager, so no value ever lands in argv or a file.
#
# One-time prerequisites:
#   gcloud config configurations activate hienhoa
#   billing enabled on the project (console.cloud.google.com/billing)
#
#   ./deploy_cloudrun.sh
set -euo pipefail
cd "$(dirname "$0")"

PROJECT="hienhoa-voice"
REGION="${REGION:-us-west1}"   # Oregon: nearest region to Palo Alto.
                               # For Vietnamese callers, asia-southeast1
                               # (Singapore) is closer, though the OpenAI
                               # inference hop to the US is unavoidable either way.
SERVICE="voice"

command -v gcloud >/dev/null || { echo "gcloud not found" >&2; exit 1; }
[ -f formd/formd.db ] || { echo "Run: python3 formd/build_db.py first" >&2; exit 1; }

gcloud config set project "$PROJECT" >/dev/null

if ! gcloud billing projects describe "$PROJECT" --format='value(billingEnabled)' \
     2>/dev/null | grep -qi true; then
  echo "Billing is not enabled on $PROJECT." >&2
  echo "Open https://console.cloud.google.com/billing and attach a payment method." >&2
  exit 1
fi

echo "Enabling APIs (first run takes a minute)..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  --project "$PROJECT" >/dev/null

# --- secrets: 1Password -> Secret Manager, via stdin only ------------------
put_secret() {
  local name="$1" ref="$2"
  if gcloud secrets describe "$name" --project "$PROJECT" >/dev/null 2>&1; then
    op read "$ref" | tr -d '\n' \
      | gcloud secrets versions add "$name" --project "$PROJECT" --data-file=- >/dev/null
    echo "  updated $name"
  else
    op read "$ref" | tr -d '\n' \
      | gcloud secrets create "$name" --project "$PROJECT" \
          --replication-policy=automatic --data-file=- >/dev/null
    echo "  created $name"
  fi
}
echo "Syncing secrets from 1Password..."
put_secret openai-api-key "op://Employee/openai/password"
put_secret voice-access-key "op://Employee/formd-voice access/password"

# Let the runtime service account read them.
SA="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
for s in openai-api-key voice-access-key; do
  gcloud secrets add-iam-policy-binding "$s" --project "$PROJECT" \
    --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor \
    >/dev/null 2>&1 || true
done

# --- deploy ----------------------------------------------------------------
# --min-instances=1        a cold start mid-call means the caller hears silence
# --no-cpu-throttling      keep CPU allocated so a streaming socket stays fed
# --timeout=3600           Cloud Run's max; a call must never be cut by a timeout
# --concurrency=40         one process, long-lived sockets, so keep this modest
echo "Deploying..."
gcloud run deploy "$SERVICE" \
  --project "$PROJECT" --region "$REGION" \
  --source . \
  --allow-unauthenticated \
  --min-instances=1 --max-instances=3 \
  --cpu=1 --memory=512Mi --no-cpu-throttling \
  --timeout=3600 --concurrency=40 \
  --set-env-vars="REALTIME_MODEL=gpt-realtime-2.1,REALTIME_VOICE=marin,MAX_SESSIONS_PER_HOUR=20,MAX_TOOLS_PER_HOUR=600" \
  --set-secrets="OPENAI_API_KEY=openai-api-key:latest,ACCESS_KEY=voice-access-key:latest"

URL=$(gcloud run services describe "$SERVICE" --project "$PROJECT" \
        --region "$REGION" --format='value(status.url)')
echo
echo "Live:   $URL"
echo "Health: $URL/healthz"
echo
echo "To move voice.hienhoa.com off the laptop tunnel and onto this:"
echo "  gcloud run domain-mappings create --service=$SERVICE --domain=voice.hienhoa.com --region=$REGION"
echo "  then update the Cloudflare CNAME to the target it prints (proxy OFF)."

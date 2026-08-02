#!/usr/bin/env bash
# Deploy FoleyFlow to Cloud Run with secrets from Secret Manager.
#
# Non-secret config: env.public.yaml (or --env-file).
# Secrets: Secret Manager via --set-secrets (never plain text in env files).
#
# IMPORTANT: gcloud's --set-secrets replaces the *entire* secret env set on the
# service with the list you pass. Omitting a secret from this script's SECRETS
# array removes it from the next revision. Prefer this script (or always pass
# the full four-secret CSV) over ad-hoc --set-secrets with a partial list.
# To add a secret, append it here AND grant the runtime SA secretAccessor.
#
# Prerequisites:
#   - gcloud auth, project access
#   - secrets already created (scripts/setup_secrets.sh)
#   - runtime SA has roles/secretmanager.secretAccessor on each secret
#
# Usage:
#   ./scripts/deploy.sh
#   ./scripts/deploy.sh --project backblaze-504305 --region us-east4
#   ./scripts/deploy.sh --env-file env.public.yaml
#   ./scripts/deploy.sh --no-source   # config-only update (image unchanged)
#   ./scripts/deploy.sh --skip-smoke # deploy without post-deploy smoke (emergency)
#
# This script never embeds or prints secret values.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="${GCP_PROJECT:-backblaze-504305}"
REGION="${GCP_REGION:-us-east4}"
SERVICE="${CLOUD_RUN_SERVICE:-foleyflow}"
ENV_FILE="${ROOT}/env.public.yaml"
SOURCE=1
SKIP_SMOKE=0
EXTRA_ARGS=()

usage() {
  cat <<'EOF'
Usage: deploy.sh [options]

  --project PROJECT     GCP project (default: backblaze-504305)
  --region REGION       Cloud Run region (default: us-east4)
  --service NAME        Service name (default: foleyflow)
  --env-file PATH       Non-secret env YAML (default: env.public.yaml)
  --no-source           Do not rebuild from source; only update env/secrets/flags
  --skip-smoke          Skip post-deploy smoke_deploy.py (emergency only)
  --help                Show this help

Secrets (must already exist in Secret Manager):
  B2_APPLICATION_KEY          ← foleyflow-b2-application-key:latest
  B2_KEY_ID                   ← foleyflow-b2-key-id:latest
  GEMINI_API_KEY              ← foleyflow-gemini-api-key:latest
  FOLEYFLOW_SFX_WORKER_TOKEN  ← foleyflow-sfx-worker-token:latest

  NOTE: --set-secrets replaces the full secret set on the service. This script
  always passes all four; a partial --set-secrets elsewhere will drop omitted
  secrets from the next revision.

Create/rotate them with: ./scripts/setup_secrets.sh --env-file .env

After a successful deploy the script resolves the service URL and runs:
  uv run python scripts/smoke_deploy.py --base-url "$URL" --expect-sha "$GIT_SHA"
Deploy fails if smoke fails (unless --skip-smoke).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --no-source) SOURCE=0; shift ;;
    --skip-smoke) SKIP_SMOKE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) EXTRA_ARGS+=("$1"); shift ;;
  esac
done

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing non-secret env file: $ENV_FILE" >&2
  echo "Copy env.public.yaml or pass --env-file." >&2
  exit 1
fi

# Refuse env files that look like they still contain secrets.
if grep -E '^(B2_APPLICATION_KEY|B2_KEY_ID|GEMINI_API_KEY|FOLEYFLOW_SFX_WORKER_TOKEN|FAL_KEY|OPENAI_API_KEY|ELEVENLABS_API_KEY):' \
  "$ENV_FILE" >/dev/null 2>&1; then
  echo "Refusing to deploy: $ENV_FILE still declares secret keys as plain env." >&2
  echo "Move those to Secret Manager (scripts/setup_secrets.sh) and remove them from the file." >&2
  exit 1
fi

# Stamp identity into a temp env file so we do not mutate the committed public file.
STAMP_FILE="$(mktemp)"
trap 'rm -f "$STAMP_FILE"' EXIT
cp "$ENV_FILE" "$STAMP_FILE"
# Drop any prior stamp lines, then append fresh ones.
grep -vE '^(FOLEYFLOW_GIT_SHA|FOLEYFLOW_BUILD_TIME):' "$STAMP_FILE" > "${STAMP_FILE}.clean" || true
mv "${STAMP_FILE}.clean" "$STAMP_FILE"
GIT_SHA="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'FOLEYFLOW_GIT_SHA: "%s"\nFOLEYFLOW_BUILD_TIME: "%s"\n' "$GIT_SHA" "$BUILD_TIME" >> "$STAMP_FILE"
echo "Deploy stamp: git_sha=$GIT_SHA build_time=$BUILD_TIME"

SECRETS=(
  "B2_APPLICATION_KEY=foleyflow-b2-application-key:latest"
  "B2_KEY_ID=foleyflow-b2-key-id:latest"
  "GEMINI_API_KEY=foleyflow-gemini-api-key:latest"
  "FOLEYFLOW_SFX_WORKER_TOKEN=foleyflow-sfx-worker-token:latest"
)
# Paid SFX tier — only mount if the secret already exists (created by
# setup_secrets.sh when ELEVENLABS_API_KEY is set in .env).
if gcloud secrets describe foleyflow-elevenlabs-api-key \
    --project="$PROJECT" >/dev/null 2>&1; then
  SECRETS+=("ELEVENLABS_API_KEY=foleyflow-elevenlabs-api-key:latest")
  echo "Including ELEVENLABS_API_KEY secret mount (paid SFX tier)."
else
  echo "Note: foleyflow-elevenlabs-api-key not in Secret Manager — paid tier"
  echo "      will be unavailable until: set ELEVENLABS_API_KEY in .env and"
  echo "      run ./scripts/setup_secrets.sh && ./scripts/deploy.sh --no-source"
fi
SECRETS_CSV="$(IFS=,; echo "${SECRETS[*]}")"

COMMON=(
  --project="$PROJECT"
  --region="$REGION"
  --env-vars-file="$STAMP_FILE"
  --set-secrets="$SECRETS_CSV"
  --no-cpu-throttling
  --min-instances=1
  --max-instances=1
  --memory=4Gi
  --cpu=2
  --timeout=300
)

if [[ "$SOURCE" -eq 1 ]]; then
  echo "Deploying $SERVICE from source in $ROOT …"
  gcloud run deploy "$SERVICE" \
    --source="$ROOT" \
    --allow-unauthenticated \
    "${COMMON[@]}" \
    "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
else
  echo "Updating $SERVICE config only (no source rebuild) …"
  # services update uses --update-secrets / --set-secrets similarly.
  # Remember --set-secrets replaces the entire secret set (see header).
  gcloud run services update "$SERVICE" \
    "${COMMON[@]}" \
    "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
fi

echo
echo "Verify secrets are references, not values:"
echo "  gcloud run services describe $SERVICE --project $PROJECT --region $REGION \\"
echo "    --format='yaml(spec.template.spec.containers[0].env)'"
echo "Expect valueFrom.secretKeyRef for B2_APPLICATION_KEY, B2_KEY_ID,"
echo "GEMINI_API_KEY, FOLEYFLOW_SFX_WORKER_TOKEN."

# Post-deploy smoke: same checks operators run by hand (health, storage,
# worker, git_sha vs the commit just stamped, ledger_writable, one read).
if [[ "$SKIP_SMOKE" -eq 1 ]]; then
  echo
  echo "WARNING: --skip-smoke set; not running scripts/smoke_deploy.py."
  echo "Run it manually before treating this deploy as demo-ready."
else
  echo
  echo "Resolving service URL for post-deploy smoke …"
  URL="$(gcloud run services describe "$SERVICE" \
    --project="$PROJECT" \
    --region="$REGION" \
    --format='value(status.url)')"
  if [[ -z "$URL" ]]; then
    echo "error: could not resolve Cloud Run URL for $SERVICE" >&2
    exit 1
  fi
  echo "Running smoke against $URL (expect_sha=$GIT_SHA) …"
  # Fail the whole deploy if smoke fails — a green gcloud deploy with a stale
  # revision or unwritable ledger is not a successful ship.
  (
    cd "$ROOT"
    uv run python scripts/smoke_deploy.py \
      --base-url "$URL" \
      --expect-sha "$GIT_SHA"
  )
fi

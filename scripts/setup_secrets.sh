#!/usr/bin/env bash
# Create / rotate FoleyFlow Secret Manager secrets and grant the Cloud Run
# runtime service account secretAccessor.
#
# Values are read from the environment or from a local .env file — never
# passed on the command line, never written into the repo.
#
# Usage:
#   # from a shell that already has the secrets exported, or with .env present:
#   ./scripts/setup_secrets.sh
#   ./scripts/setup_secrets.sh --env-file /path/to/.env
#   ./scripts/setup_secrets.sh --project backblaze-504305
#
# Does not print secret values.

set -euo pipefail

PROJECT="${GCP_PROJECT:-backblaze-504305}"
ENV_FILE=""
REGION="${GCP_REGION:-us-east4}"
SERVICE="${CLOUD_RUN_SERVICE:-foleyflow}"

usage() {
  cat <<'EOF'
Usage: setup_secrets.sh [--project PROJECT] [--env-file PATH] [--region REGION] [--service SERVICE]

Creates (if missing) and adds a new version for each FoleyFlow deploy secret,
then grants roles/secretmanager.secretAccessor on each secret to the Cloud Run
runtime service account.

Secret names (Secret Manager → Cloud Run env):
  foleyflow-b2-application-key  → B2_APPLICATION_KEY
  foleyflow-b2-key-id           → B2_KEY_ID
  foleyflow-gemini-api-key      → GEMINI_API_KEY
  foleyflow-sfx-worker-token    → FOLEYFLOW_SFX_WORKER_TOKEN

Values are taken from the process environment, or from --env-file (KEY=value
lines). Nothing is echoed.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -n "$ENV_FILE" ]]; then
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "env file not found: $ENV_FILE" >&2
    exit 1
  fi
  # shellcheck disable=SC1090
  set -a
  # Only load KEY=value lines; skip comments/blank. Do not print.
  # Use a subshell-safe parse: export matching keys only.
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      val="${BASH_REMATCH[2]}"
      # strip surrounding single/double quotes
      if [[ "$val" =~ ^\"(.*)\"$ ]]; then val="${BASH_REMATCH[1]}"; fi
      if [[ "$val" =~ ^\'(.*)\'$ ]]; then val="${BASH_REMATCH[1]}"; fi
      export "$key=$val"
    fi
  done < "$ENV_FILE"
  set +a
elif [[ -f .env ]]; then
  ENV_FILE=.env
  exec "$0" --project "$PROJECT" --region "$REGION" --service "$SERVICE" --env-file .env
fi

require_var() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required value: $name (export it or pass --env-file)" >&2
    exit 1
  fi
}

require_var B2_APPLICATION_KEY
require_var B2_KEY_ID
require_var GEMINI_API_KEY
require_var FOLEYFLOW_SFX_WORKER_TOKEN

# Ensure API is enabled (idempotent).
gcloud services enable secretmanager.googleapis.com --project="$PROJECT" >/dev/null

ensure_secret() {
  local secret_name="$1"
  local env_name="$2"
  local value="${!env_name}"

  if ! gcloud secrets describe "$secret_name" --project="$PROJECT" >/dev/null 2>&1; then
    echo "Creating secret: $secret_name"
    gcloud secrets create "$secret_name" \
      --project="$PROJECT" \
      --replication-policy=automatic
  else
    echo "Secret exists: $secret_name (adding new version)"
  fi
  printf '%s' "$value" | gcloud secrets versions add "$secret_name" \
    --project="$PROJECT" \
    --data-file=-
  echo "  version added for $secret_name"
}

ensure_secret foleyflow-b2-application-key B2_APPLICATION_KEY
ensure_secret foleyflow-b2-key-id B2_KEY_ID
ensure_secret foleyflow-gemini-api-key GEMINI_API_KEY
ensure_secret foleyflow-sfx-worker-token FOLEYFLOW_SFX_WORKER_TOKEN

# Runtime SA for the Cloud Run service (default compute SA if describe fails).
SA="$(
  gcloud run services describe "$SERVICE" \
    --project="$PROJECT" \
    --region="$REGION" \
    --format='value(spec.template.spec.serviceAccountName)' 2>/dev/null || true
)"
if [[ -z "$SA" ]]; then
  PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
  SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
  echo "Service $SERVICE not found; using default compute SA: $SA"
else
  echo "Cloud Run runtime SA: $SA"
fi

for secret_name in \
  foleyflow-b2-application-key \
  foleyflow-b2-key-id \
  foleyflow-gemini-api-key \
  foleyflow-sfx-worker-token
do
  echo "Granting secretAccessor on $secret_name → $SA"
  gcloud secrets add-iam-policy-binding "$secret_name" \
    --project="$PROJECT" \
    --member="serviceAccount:${SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet >/dev/null
done

echo
echo "Done. Next: deploy with scripts/deploy.sh (uses --set-secrets, not plain env)."
echo "Verify: gcloud run services describe $SERVICE --region $REGION --project $PROJECT \\"
echo "  --format='yaml(spec.template.spec.containers[0].env)'"
echo "must show valueFrom.secretKeyRef for the four secret env vars, never raw values."

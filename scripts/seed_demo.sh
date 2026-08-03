#!/usr/bin/env bash
# Seed B2 evidence for Task 15: prewarm stems, seed timeline cache, create
# duel + inhaler projects end-to-end on the deployed API.
#
# Does NOT delete existing projects. Does NOT commit video assets.
#
# Prerequisites:
#   - .env with B2_* / FOLEYFLOW_CACHE_BACKEND (for prewarm + timeline seed)
#   - uv + foleyflow installable
#   - Source videos (not in-repo). Set DUEL_VIDEO and INHALER_VIDEO to the
#     silent source clips (or pass them as positional args). No machine-local
#     default paths — those belong on the operator's machine, not in the repo.
#
# Usage:
#   export FOLEYFLOW_API=https://foleyflow-hinblugpsa-uk.a.run.app
#   export FOLEYFLOW_SFX_TIER=test
#   export DUEL_VIDEO=/path/to/duel_kling.mp4
#   export INHALER_VIDEO=/path/to/inhaler_kling.mp4
#   bash scripts/seed_demo.sh
#   bash scripts/seed_demo.sh /path/duel.mp4 /path/inhaler.mp4
#   bash scripts/seed_demo.sh --dry-run   # prewarm dry-run + list only

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API="${FOLEYFLOW_API:-https://foleyflow-hinblugpsa-uk.a.run.app}"
export FOLEYFLOW_API="$API"
export FOLEYFLOW_SFX_TIER="${FOLEYFLOW_SFX_TIER:-test}"

DUEL_TL="${DUEL_TL:-$ROOT/samples/timelines/duel_kling.json}"
INHALER_TL="${INHALER_TL:-$ROOT/samples/timelines/inhaler_kling.json}"

DRY_RUN=0
POSITIONAL=()
for arg in "$@"; do
  if [[ "$arg" == "--dry-run" ]]; then
    DRY_RUN=1
  else
    POSITIONAL+=("$arg")
  fi
done

if [[ ${#POSITIONAL[@]} -ge 1 ]]; then
  DUEL_VIDEO="${POSITIONAL[0]}"
fi
if [[ ${#POSITIONAL[@]} -ge 2 ]]; then
  INHALER_VIDEO="${POSITIONAL[1]}"
fi

# Require explicit paths — do not fall back to hard-coded machine-local defaults.
if [[ -z "${DUEL_VIDEO:-}" || -z "${INHALER_VIDEO:-}" ]]; then
  echo "error: DUEL_VIDEO and INHALER_VIDEO must be set (env or args)." >&2
  echo "  export DUEL_VIDEO=/path/to/duel_kling.mp4" >&2
  echo "  export INHALER_VIDEO=/path/to/inhaler_kling.mp4" >&2
  echo "  # or: bash scripts/seed_demo.sh /path/duel.mp4 /path/inhaler.mp4" >&2
  exit 2
fi

echo "== Auteur demo evidence seed =="
echo "API:  $FOLEYFLOW_API"
echo "tier: $FOLEYFLOW_SFX_TIER"
echo "duel: $DUEL_VIDEO"
echo "inhaler: $INHALER_VIDEO"
echo

uv run foleyflow health
echo

echo "-- prewarm: inhaler (generate misses) --"
if [[ "$DRY_RUN" -eq 1 ]]; then
  FOLEYFLOW_SFX_TIER="$FOLEYFLOW_SFX_TIER" uv run foleyflow prewarm "$INHALER_TL" --dry-run
else
  FOLEYFLOW_SFX_TIER="$FOLEYFLOW_SFX_TIER" uv run foleyflow prewarm "$INHALER_TL"
fi
echo

echo "-- prewarm: duel (expect full cache) --"
FOLEYFLOW_SFX_TIER="$FOLEYFLOW_SFX_TIER" uv run foleyflow prewarm "$DUEL_TL" --dry-run
echo

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "-- dry-run: skipping timeline seed / create / mix / export --"
  uv run foleyflow list
  exit 0
fi

for pair in "$DUEL_VIDEO|$DUEL_TL" "$INHALER_VIDEO|$INHALER_TL"; do
  video="${pair%%|*}"
  tl="${pair##*|}"
  if [[ ! -f "$video" ]]; then
    echo "error: missing source video: $video" >&2
    echo "Set DUEL_VIDEO / INHALER_VIDEO to the silent source clips." >&2
    exit 2
  fi
  echo "-- seed timeline cache: $(basename "$video") --"
  uv run python scripts/seed_timeline_cache.py --video "$video" --timeline "$tl"
  echo
done

create_mix_export() {
  local video="$1" title="$2"
  echo "-- create: $title --"
  # Capture project id from "project proj_…" line.
  local out project_id
  out="$(uv run foleyflow create "$video" --title "$title" --analyze --wait)"
  echo "$out"
  project_id="$(printf '%s\n' "$out" | awk '/^project /{print $2; exit}')"
  if [[ -z "$project_id" ]]; then
    echo "error: could not parse project id from create output" >&2
    exit 1
  fi
  echo "-- mix $project_id --"
  uv run foleyflow mix "$project_id" --tier full --wait
  echo "-- export $project_id --"
  if ! uv run foleyflow export "$project_id" --wait; then
    echo "export blocked; retrying with --override-blocking"
    uv run foleyflow export "$project_id" --override-blocking --wait
  fi
  echo "-- show $project_id --"
  uv run foleyflow show "$project_id"
  echo
  echo "PROJECT_ID=$project_id"
}

create_mix_export "$DUEL_VIDEO" "Desert duel (Kling)"
create_mix_export "$INHALER_VIDEO" "The Inhaler (Kling)"

echo "-- list --"
uv run foleyflow list
echo
echo "-- quota (should still be 0 Gemini used if cache seeds held) --"
uv run foleyflow quota
echo
echo "Done. See scripts/demo_evidence.md for acceptance checklist."

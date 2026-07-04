#!/usr/bin/env bash
# Background sync loop for docker compose profile "scheduler".
# Runs ghdcbot run-once on an interval; skips if a previous run is still active.
set -euo pipefail

CONFIG="${GITCORD_CONFIG:-/app/config/config.yaml}"
INTERVAL="${GITCORD_SYNC_INTERVAL_SECONDS:-21600}"
LOCK_FILE="${GITCORD_SYNC_LOCK_FILE:-/data/run-once.lock}"
MIN_SLEEP="${GITCORD_SYNC_MIN_SLEEP_SECONDS:-60}"

mkdir -p "$(dirname "$LOCK_FILE")"

echo "Gitcord sync loop: config=$CONFIG interval=${INTERVAL}s"

while true; do
  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  start_sec="$(date +%s)"
  echo "[$started_at] scheduled run-once starting"

  if flock -n "$LOCK_FILE" bash -c "ghdcbot --config \"$CONFIG\" preflight-sync && ghdcbot --config \"$CONFIG\" run-once"; then
    echo "[$started_at] scheduled run-once finished OK"
  else
    exit_code=$?
    if [[ "$exit_code" -eq 1 ]]; then
      echo "[$started_at] scheduled run-once skipped (another run-once is active)"
    else
      echo "[$started_at] scheduled run-once failed (exit $exit_code)"
    fi
  fi

  elapsed=$(( $(date +%s) - start_sec ))
  wait_sec=$(( INTERVAL - elapsed ))
  if (( wait_sec < MIN_SLEEP )); then
    wait_sec=$MIN_SLEEP
  fi
  echo "[$started_at] sleeping ${wait_sec}s until next run"
  sleep "$wait_sec"
done

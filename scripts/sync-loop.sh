#!/usr/bin/env bash
# Background sync loop for docker compose profile "scheduler".
# Runs ghdcbot run-once on an interval; skips if a previous run is still active.
set -euo pipefail

CONFIG="${GITCORD_CONFIG:-/app/config/config.yaml}"
INTERVAL="${GITCORD_SYNC_INTERVAL_SECONDS:-21600}"
LOCK_FILE="${GITCORD_SYNC_LOCK_FILE:-/data/run-once.lock}"
MIN_SLEEP="${GITCORD_SYNC_MIN_SLEEP_SECONDS:-60}"
# flock -n cannot acquire lock (distinct from ghdcbot failure exit codes).
LOCK_BUSY_EXIT=200

mkdir -p "$(dirname "$LOCK_FILE")"

echo "Gitcord sync loop: config=$CONFIG interval=${INTERVAL}s"

while true; do
  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  start_sec="$(date +%s)"
  echo "[$started_at] scheduled run-once starting"

  set +e
  (
    flock -n 9 || exit "$LOCK_BUSY_EXIT"
    ghdcbot --config "$CONFIG" preflight-sync
    ghdcbot --config "$CONFIG" run-once
  ) 9>"$LOCK_FILE"
  exit_code=$?
  set -e

  if [[ "$exit_code" -eq 0 ]]; then
    echo "[$started_at] scheduled run-once finished OK"
  elif [[ "$exit_code" -eq "$LOCK_BUSY_EXIT" ]]; then
    echo "[$started_at] scheduled run-once skipped (another run-once is active)"
  else
    echo "[$started_at] scheduled run-once failed (exit $exit_code)"
  fi

  elapsed=$(( $(date +%s) - start_sec ))
  wait_sec=$(( INTERVAL - elapsed ))
  if (( wait_sec < MIN_SLEEP )); then
    wait_sec=$MIN_SLEEP
  fi
  echo "[$started_at] sleeping ${wait_sec}s until next run"
  sleep "$wait_sec"
done

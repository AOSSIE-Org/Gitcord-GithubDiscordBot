#!/usr/bin/env bash
# One-shot run-once for host cron or manual use (Docker deployment).
# Example crontab (every 6 hours):
#   0 */6 * * * cd /path/to/Gitcord-GithubDiscordBot && ./scripts/scheduled-run-once.sh >> /var/log/gitcord-sync.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONFIG_HOST="${GITCORD_CONFIG:-config/config.yaml}"
CONFIG_BASENAME="$(basename "$CONFIG_HOST")"
CONFIG_CONTAINER="/app/config/${CONFIG_BASENAME}"
LOCK_FILE="${GITCORD_SYNC_LOCK_FILE:-/data/run-once.lock}"
# flock -n cannot acquire lock (distinct from ghdcbot failure exit codes).
LOCK_BUSY_EXIT=200

if [[ ! -f "$CONFIG_HOST" ]]; then
  echo "Config not found: $CONFIG_HOST" >&2
  echo "Copy config/aussie.yaml to config/config.yaml (or set GITCORD_CONFIG)." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo ".env not found in $ROOT — create from .env.example" >&2
  exit 1
fi

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[$started_at] host scheduled run-once starting (config=$CONFIG_CONTAINER)"

"$ROOT/scripts/preflight-sync.sh"

# Hold a single flock across BOTH preflight-sync and run-once by keeping fd 9
# open on the lock file for the duration of the subshell, matching the
# fd-9 pattern in scripts/sync-loop.sh. The previous `flock -n LOCK CMD1 && CMD2`
# form only locked CMD1, letting a concurrent invocation race into run-once.
set +e
docker compose run --rm --no-TTY bot \
  bash -c "mkdir -p \"\$(dirname \"$LOCK_FILE\")\" && ( flock -n 9 || exit $LOCK_BUSY_EXIT; ghdcbot --config \"$CONFIG_CONTAINER\" preflight-sync && ghdcbot --config \"$CONFIG_CONTAINER\" run-once; ) 9>\"$LOCK_FILE\""
exit_code=$?
set -e

if [[ "$exit_code" -eq 0 ]]; then
  echo "[$started_at] host scheduled run-once finished OK"
elif [[ "$exit_code" -eq "$LOCK_BUSY_EXIT" ]]; then
  echo "[$started_at] host scheduled run-once skipped (another run-once is active)"
  exit 0
else
  echo "[$started_at] host scheduled run-once failed (exit $exit_code)" >&2
  exit "$exit_code"
fi

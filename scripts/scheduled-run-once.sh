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

docker compose run --rm --no-TTY bot \
  bash -c "mkdir -p \"\$(dirname \"$LOCK_FILE\")\" && flock -n \"$LOCK_FILE\" ghdcbot --config \"$CONFIG_CONTAINER\" preflight-sync && ghdcbot --config \"$CONFIG_CONTAINER\" run-once"

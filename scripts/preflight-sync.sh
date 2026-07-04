#!/usr/bin/env bash
# Fail fast before run-once if config would bulk-assign issues or request PR reviews.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONFIG_HOST="${GITCORD_CONFIG:-config/config.yaml}"
CONFIG_BASENAME="$(basename "$CONFIG_HOST")"
CONFIG_CONTAINER="/app/config/${CONFIG_BASENAME}"

if [[ ! -f "$CONFIG_HOST" ]]; then
  echo "Config not found: $CONFIG_HOST" >&2
  exit 1
fi

if [[ -f .env ]]; then
  docker compose run --rm --no-TTY bot \
    --config "$CONFIG_CONTAINER" preflight-sync
else
  ghdcbot --config "$CONFIG_HOST" preflight-sync
fi

#!/usr/bin/env bash
# Fly.io entrypoint: runs as root, prepares the volume, then drops privileges.
#
# Fly volumes are mounted root-owned, but Gitcord runs as `appuser` and must write
# SQLite state, reports and the sync lock under /data. We fix ownership here and then
# exec the supervisor as appuser, so no Gitcord code ever runs as root.
set -euo pipefail

DATA_DIR="${GITCORD_DATA_DIR:-/data}"
CONFIG="${GITCORD_CONFIG:-/app/config/config.yaml}"

if [[ ! -f "$CONFIG" ]]; then
  echo "fly-start: config file not found: $CONFIG" >&2
  echo "fly-start: create config/config.yaml (from config/docker-example.yaml) before 'fly deploy';" >&2
  echo "fly-start: fly.toml [[files]] ships it to the machine. See docs/FLY.md." >&2
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  mkdir -p "$DATA_DIR"
  app_uid="$(id -u appuser)"
  if [[ "$(stat -c %u "$DATA_DIR")" != "$app_uid" ]]; then
    echo "fly-start: fixing ownership of $DATA_DIR for appuser"
    chown -R appuser:appuser "$DATA_DIR"
  fi
  exec setpriv --reuid=appuser --regid=appuser --init-groups \
    env HOME=/home/appuser bash /app/scripts/fly-supervisor.sh
fi

# Already unprivileged (e.g. local test run): go straight to the supervisor.
exec bash /app/scripts/fly-supervisor.sh

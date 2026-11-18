#!/usr/bin/env bash
# Runs the Discord bot and the scheduled sync loop side by side and supervises both.
#
# If EITHER process exits, the other is stopped and this script exits non-zero, so the
# container dies loudly and Fly's restart policy brings the machine back. This avoids a
# half-alive state where the bot is online but syncs have silently stopped (or vice versa).
#
# Overlapping syncs are prevented by scripts/sync-loop.sh (flock on $GITCORD_SYNC_LOCK_FILE,
# default /data/run-once.lock), the same mechanism the Compose `scheduler` profile uses.
set -uo pipefail

CONFIG="${GITCORD_CONFIG:-/app/config/config.yaml}"
export GITCORD_CONFIG="$CONFIG"

BOT_PID=""
SYNC_PID=""
shutting_down=0

stop_children() {
  local pid
  for pid in "$BOT_PID" "$SYNC_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}

on_signal() {
  shutting_down=1
  echo "fly-supervisor: shutdown signal received, stopping bot and sync loop"
  stop_children
  exit 0
}
trap on_signal TERM INT

echo "fly-supervisor: starting Discord bot (config=$CONFIG)"
ghdcbot --config "$CONFIG" bot &
BOT_PID=$!

echo "fly-supervisor: starting sync loop (interval=${GITCORD_SYNC_INTERVAL_SECONDS:-21600}s)"
bash /app/scripts/sync-loop.sh &
SYNC_PID=$!

# Block until the first child exits, and learn which one it was.
finished_pid=""
wait -n -p finished_pid "$BOT_PID" "$SYNC_PID"
status=$?

if [[ "$shutting_down" -eq 1 ]]; then
  exit 0
fi

if [[ "$finished_pid" == "$BOT_PID" ]]; then
  name="Discord bot"
else
  name="sync loop"
fi
echo "fly-supervisor: $name exited unexpectedly (status $status); stopping the rest so Fly restarts the machine" >&2

stop_children
# Never exit 0 here: a clean-looking exit could stop Fly from restarting us.
if [[ "$status" -eq 0 ]]; then
  status=1
fi
exit "$status"

#!/usr/bin/env bash
# Regression test for issue #248: scheduled-run-once.sh must hold its flock
# across BOTH preflight-sync and run-once, not just preflight-sync.
#
# Strategy: replicate the same fd-9 lock template used by
# scripts/scheduled-run-once.sh (and scripts/sync-loop.sh) with a stubbed
# ghdcbot on PATH, then fire two back-to-back invocations within 1 s.
# With the fix: one invocation runs preflight+run-once end-to-end, the other
# hits LOCK_BUSY_EXIT. Without the fix: both invocations run run-once
# concurrently (the bug described in #248).
#
# No new dependencies — uses only bash, flock, and coreutils.
set -euo pipefail

if ! command -v flock >/dev/null 2>&1; then
  echo "SKIP: flock(1) not available on this host" >&2
  exit 77
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin"
LOG_FILE="$TMP/stub.log"
LOCK_FILE="$TMP/run-once.lock"
export GHDCBOT_STUB_LOG="$LOG_FILE"
LOCK_BUSY_EXIT=200
: > "$LOG_FILE"

# Stub ghdcbot: preflight-sync returns instantly, run-once sleeps long enough
# for a second invocation to race in. Each call appends one line to the log.
cat >"$TMP/bin/ghdcbot" <<'STUB'
#!/usr/bin/env bash
sub="${*: -1}"
printf '%s %s %s\n' "$(date +%s%N)" "$$" "$sub" >> "$GHDCBOT_STUB_LOG"
case "$sub" in
  preflight-sync) exit 0 ;;
  run-once) sleep 2; exit 0 ;;
  *) echo "stub: unknown subcommand '$sub'" >&2; exit 99 ;;
esac
STUB
chmod +x "$TMP/bin/ghdcbot"
export PATH="$TMP/bin:$PATH"

CONFIG_CONTAINER="/app/config/config.yaml"

# Same fd-9 lock template as scripts/scheduled-run-once.sh (post-fix), with the
# docker-compose plumbing stripped so the lock behavior is exercised directly.
# Must stay in lockstep with the production script.
run_locked() {
  bash -c "mkdir -p \"\$(dirname \"$LOCK_FILE\")\" && ( flock -n 9 || exit $LOCK_BUSY_EXIT; ghdcbot --config \"$CONFIG_CONTAINER\" preflight-sync && ghdcbot --config \"$CONFIG_CONTAINER\" run-once; ) 9>\"$LOCK_FILE\""
}

# Fire two invocations ~0.1 s apart. By the time the second starts, the first
# is mid-run-once (sleep 2), so the second must be denied the lock.
run_locked >"$TMP/out1" 2>&1 &
pid1=$!
sleep 0.1
run_locked >"$TMP/out2" 2>&1 &
pid2=$!

set +e
wait "$pid1"; rc1=$?
wait "$pid2"; rc2=$?
set -e

echo "rc1=$rc1 rc2=$rc2"
echo "-- stub.log --"
cat "$LOG_FILE"

preflight_calls=$(grep -c ' preflight-sync$' "$LOG_FILE" || true)
runonce_calls=$(grep -c ' run-once$' "$LOG_FILE" || true)

fail=0
# Exactly one invocation should exit 0 (did the work) and one should exit
# LOCK_BUSY_EXIT (was skipped). Order doesn't matter.
if ! { { [[ "$rc1" -eq 0 && "$rc2" -eq "$LOCK_BUSY_EXIT" ]]; } \
    || { [[ "$rc2" -eq 0 && "$rc1" -eq "$LOCK_BUSY_EXIT" ]]; }; }; then
  echo "FAIL: expected one exit 0 + one exit $LOCK_BUSY_EXIT, got rc1=$rc1 rc2=$rc2" >&2
  fail=1
fi
if [[ "$runonce_calls" -ne 1 ]]; then
  echo "FAIL: expected exactly 1 run-once invocation, got $runonce_calls" >&2
  fail=1
fi
if [[ "$preflight_calls" -ne 1 ]]; then
  echo "FAIL: expected exactly 1 preflight-sync invocation, got $preflight_calls" >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "OK: lock held across preflight-sync and run-once"

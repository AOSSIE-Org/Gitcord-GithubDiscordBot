#!/usr/bin/env bash
# Regression test for issue #248: scheduled-run-once.sh must hold its flock
# across BOTH preflight-sync and run-once, not just preflight-sync.
#
# This harness exercises the REAL production script
# (scripts/scheduled-run-once.sh) through a controlled Docker stub so the
# fd-9 lock sequence is actually executed — not a copy pasted into the test.
# The stub:
#   * records every `docker compose run ...` invocation for argument assertions
#   * parses the `-c <body>` payload and runs it on the host via plain bash
# A ghdcbot stub is installed on PATH so the body's `ghdcbot` calls land in
# this test's coordination layer instead of the real binary.
#
# Coordination: the ghdcbot stub signals (via a sentinel file) when it enters
# `run-once`, then blocks until an explicit release-file appears. The parent
# races a second invocation of the production script into the locked section,
# asserts it hits LOCK_BUSY_EXIT, releases the first, and checks the stub +
# docker call counts. All waits are bounded (wait_for_file 10s, stub wait
# ~20s ceiling) so coordination failures surface as FAIL instead of hanging.
#
# No new dependencies — uses only bash, flock, and coreutils.
set -euo pipefail

if ! command -v flock >/dev/null 2>&1; then
  echo "SKIP: flock(1) not available on this host" >&2
  exit 77
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROD_SCRIPT="$REPO_ROOT/scripts/scheduled-run-once.sh"

# Bounded-wait helper: polls for a file's existence with a hard timeout so
# the test reports a clear failure instead of hanging when coordination
# between the two invocations breaks.
wait_for_file() {
  local file=$1 timeout=${2:-10}
  local start=$SECONDS
  while [ ! -e "$file" ]; do
    if [ $((SECONDS - start)) -ge $timeout ]; then
      echo "FAIL: timeout waiting for $file" >&2
      return 1
    fi
    sleep 0.1
  done
}

# Sandboxed repo copy so the production script's `cd $ROOT`, config/.env
# checks, and sibling `preflight-sync.sh` call don't need real repo state.
SANDBOX="$TMP/repo"
mkdir -p "$SANDBOX/scripts" "$SANDBOX/config"
cp "$PROD_SCRIPT" "$SANDBOX/scripts/scheduled-run-once.sh"
chmod +x "$SANDBOX/scripts/scheduled-run-once.sh"
cat >"$SANDBOX/scripts/preflight-sync.sh" <<'PREFLIGHT'
#!/usr/bin/env bash
# Host-side preflight is a no-op for this test; the regression is purely
# about the docker-side lock held across preflight+run-once.
exit 0
PREFLIGHT
chmod +x "$SANDBOX/scripts/preflight-sync.sh"
: >"$SANDBOX/config/config.yaml"
: >"$SANDBOX/.env"

# Shared paths wired into both stubs via env vars.
LOCK_FILE="$TMP/run-once.lock"
STUB_LOG="$TMP/ghdcbot-stub.log"
DOCKER_LOG="$TMP/docker-stub.log"
STARTED_FLAG="$TMP/runonce.started"
RELEASE_FLAG="$TMP/runonce.release"
export GHDCBOT_STUB_LOG="$STUB_LOG"
export GHDCBOT_STARTED_FLAG="$STARTED_FLAG"
export GHDCBOT_RELEASE_FLAG="$RELEASE_FLAG"
export DOCKER_STUB_LOG="$DOCKER_LOG"
export GITCORD_SYNC_LOCK_FILE="$LOCK_FILE"
# Pin the config path to the sandbox file both invocations expect, so a
# developer who exports GITCORD_CONFIG (e.g. config/aussie.yaml) in their
# shell doesn't make the production script bail on "Config not found" before
# it ever reaches docker.
export GITCORD_CONFIG="config/config.yaml"
: >"$STUB_LOG"
: >"$DOCKER_LOG"

# ghdcbot stub: preflight-sync returns instantly; run-once signals entry and
# blocks until the release flag appears, with a bounded ceiling so a missed
# release can't hang the harness indefinitely.
mkdir -p "$TMP/bin"
cat >"$TMP/bin/ghdcbot" <<'GHDCBOT'
#!/usr/bin/env bash
sub="${*: -1}"
printf '%s %s %s\n' "$(date +%s%N)" "$$" "$sub" >>"$GHDCBOT_STUB_LOG"
case "$sub" in
  preflight-sync)
    exit 0
    ;;
  run-once)
    : >"$GHDCBOT_STARTED_FLAG"
    # Max 20 s wait for explicit release; longer than the second invocation
    # possibly needs, short enough that a coordination bug fails fast.
    limit=200
    while [ ! -e "$GHDCBOT_RELEASE_FLAG" ] && [ "$limit" -gt 0 ]; do
      sleep 0.1
      limit=$((limit - 1))
    done
    exit 0
    ;;
  *)
    echo "ghdcbot-stub: unknown subcommand '$sub'" >&2
    exit 99
    ;;
esac
GHDCBOT
chmod +x "$TMP/bin/ghdcbot"

# Docker stub: records every invocation for argument assertions, then for
# `compose run` extracts the `-c <body>` payload and executes it on the host
# so the production script's fd-9 pattern is exercised end-to-end.
cat >"$TMP/bin/docker" <<'DOCKER_STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$DOCKER_STUB_LOG"
if [ "${1:-}" = "compose" ] && [ "${2:-}" = "run" ]; then
  shift 2
  # Walk compose-run flags until we reach the `bot` service name, then capture
  # the `-c <body>` that follows. Known valued flags (`--entrypoint`, `-e`,
  # `--env`) consume two slots; known boolean flags consume one.
  while [ $# -gt 0 ]; do
    case "$1" in
      --rm|--no-TTY|-T|-d|--detach|--service-ports|--use-aliases|--quiet-pull|--build|--remove-orphans)
        shift
        ;;
      --entrypoint|-e|--env|--name|--user|-u|-w|--workdir|--volume|-v|--label|-l|--publish|-p)
        shift 2
        ;;
      --entrypoint=*|--env=*|--name=*|--user=*|--workdir=*|--volume=*|--label=*|--publish=*)
        shift
        ;;
      bot)
        shift
        break
        ;;
      *)
        echo "docker-stub: unexpected compose-run flag '$1'" >&2
        exit 2
        ;;
    esac
  done
  if [ "${1:-}" = "-c" ] && [ $# -ge 2 ]; then
    exec bash -c "$2"
  fi
  echo "docker-stub: expected '-c <body>' after bot, got: $*" >&2
  exit 2
fi
echo "docker-stub: unexpected call: $*" >&2
exit 2
DOCKER_STUB
chmod +x "$TMP/bin/docker"

export PATH="$TMP/bin:$PATH"

# First invocation: should acquire the lock, enter run-once, and park there
# until the parent writes RELEASE_FLAG.
bash "$SANDBOX/scripts/scheduled-run-once.sh" >"$TMP/out1" 2>&1 &
pid1=$!

# Bounded wait for the first invocation to actually be mid-run-once.
if ! wait_for_file "$STARTED_FLAG" 10; then
  kill "$pid1" 2>/dev/null || true
  wait "$pid1" 2>/dev/null || true
  echo "-- out1 --"; cat "$TMP/out1" || true
  echo "-- docker-stub.log --"; cat "$DOCKER_LOG" || true
  exit 1
fi

# Second invocation now races in while the lock is held. Must exit cleanly
# after emitting the "skipped" line (host script maps LOCK_BUSY_EXIT -> 0).
set +e
bash "$SANDBOX/scripts/scheduled-run-once.sh" >"$TMP/out2" 2>&1
rc2=$?
set -e

# Release the first invocation so it can finish its run-once and exit.
: >"$RELEASE_FLAG"
set +e
wait "$pid1"
rc1=$?
set -e

echo "rc1=$rc1 rc2=$rc2"
echo "-- ghdcbot-stub.log --"
cat "$STUB_LOG"
echo "-- docker-stub.log --"
cat "$DOCKER_LOG"
echo "-- out1 --"
cat "$TMP/out1"
echo "-- out2 --"
cat "$TMP/out2"

preflight_calls=$(grep -c ' preflight-sync$' "$STUB_LOG" || true)
runonce_calls=$(grep -c ' run-once$' "$STUB_LOG" || true)
docker_calls=$(wc -l <"$DOCKER_LOG" | tr -d ' ')

fail=0
# The first invocation did the work and must exit 0.
if [ "$rc1" -ne 0 ]; then
  echo "FAIL: first invocation should exit 0 (did the work), got $rc1" >&2
  fail=1
fi
# The second invocation hit contention and must exit 0 after the skip log.
if [ "$rc2" -ne 0 ]; then
  echo "FAIL: second invocation (contention) should exit 0 after the skip log, got $rc2" >&2
  fail=1
fi
# Production script's successful-skip output must be present on stdout.
if ! grep -q 'skipped (another run-once is active)' "$TMP/out2"; then
  echo "FAIL: second invocation did not emit the expected skip message" >&2
  fail=1
fi
# Exactly one each — proves the lock held across both commands.
if [ "$preflight_calls" -ne 1 ]; then
  echo "FAIL: expected exactly 1 preflight-sync invocation, got $preflight_calls" >&2
  fail=1
fi
if [ "$runonce_calls" -ne 1 ]; then
  echo "FAIL: expected exactly 1 run-once invocation, got $runonce_calls" >&2
  fail=1
fi
# Two calls into the production script => two docker invocations captured.
if [ "$docker_calls" -ne 2 ]; then
  echo "FAIL: expected exactly 2 docker invocations, got $docker_calls" >&2
  fail=1
fi
# Both docker calls must place --entrypoint bash before the bot service name
# (fix #1 from the review — ghdcbot ENTRYPOINT override).
entrypoint_calls=$(grep -c -- '--entrypoint bash bot' "$DOCKER_LOG" || true)
if [ "$entrypoint_calls" -ne 2 ]; then
  echo "FAIL: expected both docker calls to include '--entrypoint bash bot', got $entrypoint_calls" >&2
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  exit 1
fi

echo "OK: lock held across preflight-sync and run-once; production script exercised end-to-end"

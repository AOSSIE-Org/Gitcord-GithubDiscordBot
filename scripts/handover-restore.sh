#!/usr/bin/env bash
# Back-compat wrapper — use ./scripts/gitcord-handover restore FILE.tar.gz
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
FORCE_ARGS=()
BUNDLE=""
for arg in "$@"; do
  case "$arg" in
    --force) FORCE_ARGS+=(--force) ;;
    --start)
      # Legacy flag: restore always starts stacks; do NOT treat as --force.
      ;;
    *) BUNDLE="$arg" ;;
  esac
done
if [[ -z "$BUNDLE" ]]; then
  echo "Usage: $0 [--force] /path/to/gitcord-handover-*.tar.gz" >&2
  exit 1
fi
if [[ -d "$BUNDLE" ]]; then
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/gitcord-handover-compat.XXXXXX")"
  trap 'rm -rf "$TMP"' EXIT INT TERM
  TAR="$TMP/gitcord-handover-compat.tar.gz"
  tar -czf "$TAR" -C "$BUNDLE" .
  "$ROOT/gitcord-handover" restore "${FORCE_ARGS[@]+"${FORCE_ARGS[@]}"}" "$TAR"
  exit $?
fi
exec "$ROOT/gitcord-handover" restore "${FORCE_ARGS[@]+"${FORCE_ARGS[@]}"}" "$BUNDLE"

#!/usr/bin/env bash
# Back-compat wrapper — use ./scripts/gitcord-handover restore FILE.tar.gz
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BUNDLE="${1:-}"
if [[ -z "$BUNDLE" ]]; then
  echo "Usage: $0 /path/to/gitcord-handover-*.tar.gz   (or a extracted bundle dir)" >&2
  exit 1
fi
if [[ -d "$BUNDLE" ]]; then
  # Old folder-style bundle → wrap to tar then restore
  TMP="$(mktemp -d)"
  TAR="$TMP/gitcord-handover-compat.tar.gz"
  tar -czf "$TAR" -C "$BUNDLE" .
  exec "$ROOT/gitcord-handover" restore "$TAR"
fi
exec "$ROOT/gitcord-handover" restore "$BUNDLE"

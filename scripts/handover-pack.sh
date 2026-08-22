#!/usr/bin/env bash
# Back-compat wrapper — use ./scripts/gitcord-handover pack
exec "$(cd "$(dirname "$0")" && pwd)/gitcord-handover" pack "$@"

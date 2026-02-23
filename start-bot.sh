#!/usr/bin/env bash
# Start the Gitcord Discord bot (stays in foreground; use Ctrl+C to stop)
cd "$(dirname "$0")"
exec python -m ghdcbot.cli --config config/shubh-olrd.yaml bot

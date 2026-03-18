#!/bin/sh
# Ensure /data is writable by appuser (named volume may be root-owned on first run).
chown -R appuser:appuser /data 2>/dev/null || true
exec gosu appuser ghdcbot "$@"

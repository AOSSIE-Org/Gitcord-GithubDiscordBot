# Demo Script (2–3 minutes)

## Command to run
```
python -m ghdcbot.cli --config /tmp/ghdcbot-config.yaml run-once
```

## Required env vars
```
export GITHUB_TOKEN="your_github_token"
export DISCORD_TOKEN="your_discord_token"
```

## What to point out in logs
- The run starts in dry-run or observer mode.
- Readers log permission-safe behavior (no admin required).
- Planning logs indicate whether any changes are required.
- Audit reports are written to `<data_dir>/reports`.

## What to open
Open `audit.md` and skim:
- Summary counts
- Discord role changes (if any)
- GitHub issue assignments
- GitHub PR review assignments

## Why this is safe
All decisions are planned and reported before any mutations; in dry-run and observer
mode, no writes occur. The report shows exactly what would happen.

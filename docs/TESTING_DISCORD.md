# Testing in Discord

This guide explains how to safely validate Gitcord role automation in a Discord server.

## Recommended Test Sequence

1. Keep `runtime.mode: "dry-run"` and `discord.permissions.write: false`.
2. Run a sync:
   - `./.venv/bin/python -m ghdcbot.cli --config config/my-org-config.yaml run-once`
3. Review the generated report at `<data_dir>/reports/audit.md`.
4. Verify planned role changes and identity mappings are correct.
5. Enable live updates only after review:
   - `runtime.mode: "active"`
   - `discord.permissions.write: true`
6. Run `run-once` again and confirm expected role changes in Discord.

## Discord Permission Checklist

- Bot has `Manage Roles`, `View Channels`, `Send Messages`, `Embed Links`, and `Read Message History`.
- Bot role is above any role it should assign/remove.
- Application has `Server Members Intent` enabled in Discord Developer Portal.

## Bot Command Smoke Tests

- Identity: `/link`, `/verify-link`, `/verify`, `/status`, `/unlink`
- Metrics: `/summary`, `/pr-info`
- Mentor actions (with configured role): `/request-issue`, `/assign-issue`, `/issue-requests`, `/sync`

If slash commands do not appear immediately, wait for command sync and ensure the configured `discord.guild_id` is correct.

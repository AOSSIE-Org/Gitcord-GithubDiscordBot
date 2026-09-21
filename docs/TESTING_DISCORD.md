# Testing in Discord

This guide explains how to safely validate Gitcord role automation in a Discord server.

## Recommended Test Sequence

1. **Dry-run phase (no Discord role mutations):** In your config, set:
   - `runtime.mode: "dry-run"`
   - `runtime.enable_discord_role_updates: false`
   - `discord.permissions.write: false`  
   With `enable_discord_role_updates: false`, `run-once` will not apply Discord role add/remove even if mode or permissions were mis-set.
2. Run a sync:
   - `ghdcbot --config config/config.yaml run-once`
   - Docker: `docker compose run --rm bot --config /app/config/config.yaml run-once`
3. Review the generated report at `<data_dir>/reports/audit.md`.
4. Verify planned role changes and identity mappings are correct.
5. **Live phase (apply role updates):** Only after review, set:
   - `runtime.mode: "active"`
   - `runtime.enable_discord_role_updates: true`
   - `discord.permissions.write: true`
6. Run `run-once` again and confirm expected role changes in Discord.

## Discord Permission Checklist

- Bot has `Manage Roles`, `View Channels`, `Send Messages`, `Embed Links`, and `Read Message History`.
- Bot role is above any role it should assign/remove.
- Application has `Server Members Intent` enabled in Discord Developer Portal (required for `welcome_dm_on_join` and some member lookups).
- For join-welcome DMs: set `discord.welcome_dm_on_join: true`, restart the bot, join with a test account that allows DMs from server members, and confirm **Start linking** → username modal → verify.

## Bot Command Smoke Tests

- Identity: `/link`, `/verify-link`, `/profile` (optional Discord member), `/unlink`
- Social: `/connect-social`, `/disconnect-social`
- Metrics: `/summary` (optional Discord member), `/open-prs` (required Discord member), `/pr` (member + count N, optional skip M)
- Mentor actions (with configured role): `/sync`

If slash commands do not appear immediately, wait for command sync and ensure the configured `discord.guild_id` is correct.

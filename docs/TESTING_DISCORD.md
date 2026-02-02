# Testing Gitcord in Discord

This guide walks you through testing the bot against a real Discord server: dry-run (no changes) and live role updates.

---

## 1. Prerequisites

- A Discord server where you can add a bot and manage roles.
- Bot token and guild (server) ID in your config (see [CONFIG.md](CONFIG.md)).
- GitHub org/token and at least one **identity mapping** (GitHub user ↔ Discord user ID) so the bot can score and plan role changes. You can create verified mappings **from Discord** (see [Linking from Discord](#linking-your-github-account-from-discord)) or via the CLI `link` and `verify-link`, or set `identity_mappings` in config.

---

## 2. Linking your GitHub account from Discord

You can link your GitHub account **inside Discord** using slash commands. No CLI needed.

1. **Invite the bot** with the **applications.commands** scope so slash commands work. In the [Discord Developer Portal](https://discord.com/developers/applications) → your application → **OAuth2 → URL Generator**, under **Scopes** select **bot** and **applications.commands**. Under **Bot Permissions** add at least **View Channels** and **Manage Roles** (for role updates). Use the generated URL to invite the bot to your server.

2. **Run the bot** (one process, keep it running):
   ```bash
   python -m ghdcbot --config config/your-config.yaml bot
   ```
   The bot stays online and syncs `/link` and `/verify-link` to your guild.

3. **In Discord:**
   - Use **`/link`** and enter your GitHub username. The bot replies with a **verification code** (only you see it).
   - Put that code in your **GitHub profile bio** or in a **public gist**.
   - Use **`/verify-link`** with the same GitHub username. The bot confirms the link.

After that, the same config’s `run-once` will use this verified mapping for scoring and role planning.

---

## 3. Invite the bot to your server (for run-once and roles)

1. In the [Discord Developer Portal](https://discord.com/developers/applications), open your application and go to **OAuth2 → URL Generator**.
2. Under **Scopes**, select **bot** (and **applications.commands** if you use slash commands for linking).
3. Under **Bot Permissions**, select at least:
   - **View Channels** (to see the server)
   - **Manage Roles** (required for adding/removing roles)
4. Copy the generated URL and open it in a browser. Choose your server and authorize. The bot will appear in your server’s member list (often offline until you run the CLI).

---

## 4. Bot role position (for live role updates)

Discord only allows a bot to assign roles that are **below** its own role.

- Go to **Server Settings → Roles**.
- Drag the bot’s role **above** any role it should assign (e.g. "Contributor", "Maintainer").
- Save.

---

## 5. Dry-run test (no Discord changes)

This confirms the bot can read your server and shows what it *would* do.

1. Use a config with:
   - `runtime.mode: "dry-run"` (default)
   - `discord.guild_id` set to your server ID
   - At least one identity mapping (or verified link) so someone has a score and planned roles.

2. Run:
   ```bash
   python -m ghdcbot --config config/your-config.yaml run-once
   ```

3. Check:
   - No errors in the log (the bot listed members and roles).
   - `<data_dir>/reports/audit.md` (and `audit.json`) exist and show planned Discord role add/remove actions for mapped users.

You have **tested the bot in Discord** in the sense that it read your guild and produced plans; nothing was changed in Discord.

---

## 6. Live test (actual role changes in Discord)

Only do this when you are ready for the bot to add/remove roles.

1. In your config set:
   ```yaml
   runtime:
     mode: "active"
   discord:
     permissions:
       write: true
   ```

2. Ensure:
   - The bot is in the server and its role is above the roles it will assign (see step 3).
   - `role_mappings` in config match **exact** role names that exist in the server (e.g. `Contributor`, `Maintainer`).
   - Identity mappings (or verified links) link Discord user IDs to GitHub usernames that have contribution data so the bot can compute scores and decide who gets which role.

3. Run:
   ```bash
   python -m ghdcbot --config config/your-config.yaml run-once
   ```

4. In Discord:
   - Check that users who qualify for a role now have that role.
   - Check that users who no longer qualify have the role removed (if the bot manages that role).

5. To stop live changes, set `runtime.mode: "dry-run"` and/or `discord.permissions.write: false` and run again; the bot will only read and report.

---

## 7. Troubleshooting

| Issue | What to check |
|-------|----------------|
| Bot can’t list members/roles | Bot is in the server; token has `bot` scope; bot has “View Channels” and “Manage Roles”. |
| 403 when adding/removing roles | Bot role is **above** the role you’re assigning in Server Settings → Roles. |
| No role changes in Discord | Config has `mode: "active"` and `discord.permissions.write: true`; identity mappings exist and have contribution data; `role_mappings` names match server role names exactly. |
| “Role not found” in logs | The role name in `role_mappings` must match the Discord role name exactly (case-sensitive). |

---

## 8. Summary

- **Dry-run:** Invite bot → run `run-once` → inspect audit reports. No changes in Discord.
- **Live:** Set `mode: "active"` and `discord.permissions.write: true` → put bot role above managed roles → run `run-once` → verify roles in Discord.

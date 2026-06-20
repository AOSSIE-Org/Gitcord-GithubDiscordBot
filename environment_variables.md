# Gitcord Environment Variables

Gitcord reads secrets from a `.env` file in the project root (local CLI) or via `env_file: .env` in `docker-compose.yml` (Docker). Config YAML references them as `${VAR_NAME}`; the loader expands these at startup.

---

## Required variables

| Variable | Required | Default | Where used | Description |
|----------|----------|---------|------------|-------------|
| `GITHUB_TOKEN` | **Yes** | none | `github.token` in config YAML | Fine-grained GitHub personal access token. Needs read access to org repos; write access for issue assignment, review requests, and snapshots. |
| `DISCORD_TOKEN` | **Yes** | none | `discord.token` in config YAML | Discord bot token from the [Developer Portal](https://discord.com/developers/applications) → Bot → Reset Token. |

If either variable is missing, startup fails with:

```text
Missing required environment variable: GITHUB_TOKEN
```

(or `DISCORD_TOKEN`).

**Note:** Empty values (`GITHUB_TOKEN=`) are rejected at startup. Run `ghdcbot --config config/config.yaml validate` to confirm both tokens work against the live APIs before starting the bot.

---

## Preflight validation

```bash
ghdcbot --config config/config.yaml validate
```

Checks (read-only, no bot startup):

- Config file and YAML schema
- `GITHUB_TOKEN` and `DISCORD_TOKEN` present
- GitHub `GET /user` and org/repository access
- Discord bot token and guild reachability
- Discord role names from config (when `runtime.enable_discord_role_updates: true`)

See [INSTALLATION.md](INSTALLATION.md#63-validate-setup-recommended) for Docker usage and sample output.

## Setup

```bash
cp .env.example .env
```

Edit `.env`:

```env
GITHUB_TOKEN=github_pat_xxxxxxxx
DISCORD_TOKEN=MTxxxxxxxx
```

**Security:**

- `.env` is listed in `.gitignore` — never commit it.
- Do not put tokens directly in config YAML; use `${GITHUB_TOKEN}` and `${DISCORD_TOKEN}`.
- Rotate tokens on a schedule (90 days recommended for GitHub fine-grained PATs).

---

## Variables not in `.env`

These are set in **config YAML**, not environment variables:

| Setting | Example | Purpose |
|---------|---------|---------|
| `github.org` | `my-org` | GitHub organization to scan |
| `discord.guild_id` | `123456789012345678` | Discord server ID (Developer Mode → Copy Server ID) |
| `runtime.data_dir` | `./data` (local) or `/data` (Docker) | SQLite DB and reports location |
| `runtime.mode` | `dry-run` | `dry-run`, `observer`, or `active` |

See `config/example.yaml` (local) and `config/docker-example.yaml` (Docker) for full options.

---

## Docker vs local

| Context | How env vars are loaded |
|---------|-------------------------|
| **Local CLI** | `load_dotenv()` reads `.env` from the current working directory when you run `ghdcbot`. |
| **Docker Compose** | `env_file: .env` injects variables into the container; config mount is read-only at `/app/config`. |

Use the **same** `.env` and variable names for both paths.

---

## Obsolete / unused

| Variable | Status |
|----------|--------|
| *(none)* | Gitcord currently uses only `GITHUB_TOKEN` and `DISCORD_TOKEN` from the environment. |

No other env vars are required unless you add custom `${VAR}` placeholders to your own config YAML.

---

## Related documentation

- [INSTALLATION.md](INSTALLATION.md) — full setup including PAT and Discord bot creation
- [docs/DOCKER.md](docs/DOCKER.md) — Docker-specific deployment
- [TECHNICAL_DOCUMENTATION.md](TECHNICAL_DOCUMENTATION.md) — configuration reference (Appendix A)

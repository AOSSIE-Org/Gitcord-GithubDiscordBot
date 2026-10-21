# Deploying Gitcord on Fly.io

This runs Gitcord as **one Fly machine** that hosts both the Discord bot and the scheduled sync (`run-once` every 6 hours by default). It uses `Dockerfile.fly` and `fly.toml` from the repo root; the Docker Compose / VPS setup in [DOCKER.md](DOCKER.md) is unchanged.

Gitcord only makes **outbound** HTTPS calls (Discord, GitHub), so there are no public ports, no `[http_service]`, and no custom domain.

For GitHub PAT and Discord bot creation, see [INSTALLATION.md](../INSTALLATION.md).

---

## How it differs from Compose

| Compose | Fly.io |
| ------- | ------ |
| `network_mode: host` | Not needed. Fly provides DNS and outbound networking. |
| `.env` file | `fly secrets set` (tokens are never in git or the image). |
| `./config` bind mount | `[[files]]` in `fly.toml` ships your local `config/config.yaml`. |
| `gitcord_data` named volume + `init_data` chown container | A Fly volume mounted at `/data`; `scripts/fly-start.sh` chowns it at boot. |
| Separate `bot` and `sync-scheduler` containers | One machine; `scripts/fly-supervisor.sh` runs both and restarts the machine if either dies. |

---

## Prerequisites

- [`flyctl`](https://fly.io/docs/flyctl/install/) installed and `fly auth login` done.
- A GitHub PAT and Discord bot token ([INSTALLATION.md](../INSTALLATION.md)).
- This repository cloned locally.

---

## Deploy

1. **Create your config** (the file is gitignored; it contains placeholders, not secrets):

   ```bash
   cp config/docker-example.yaml config/config.yaml
   ```

   Edit `github.org` and `discord.guild_id`. Keep `data_dir: "/data"` and keep the `${GITHUB_TOKEN}` / `${DISCORD_TOKEN}` placeholders.

2. **Pick an app name** in `fly.toml` (`app = "..."` must be globally unique) and a region (`primary_region`), then create the app without deploying:

   ```bash
   fly launch --copy-config --no-deploy
   ```

   `--copy-config` keeps this repo's `fly.toml` instead of generating a new one. Answer **No** if asked to set up a database or add services.

3. **Create the persistent volume** in the same region as `primary_region`:

   ```bash
   fly volumes create gitcord_data --size 1 --region <primary_region>
   ```

4. **Set secrets**:

   ```bash
   fly secrets set GITHUB_TOKEN="github_pat_..." DISCORD_TOKEN="..."
   ```

5. **Deploy a single machine**:

   ```bash
   fly deploy --ha=false
   ```

   A volume can only be attached to one machine, so do not scale beyond one (`fly scale count 1`).

---

## Verify

```bash
fly status
fly logs
```

In the logs you should see, in order:

- `fly-start: fixing ownership of /data for appuser` (first boot only)
- `fly-supervisor: starting Discord bot ...` and `fly-supervisor: starting sync loop ...`
- `scheduled run-once finished OK` once the first sync completes
- The bot coming online in your Discord server (slash commands sync within ~30 seconds)

Offline config check from the running machine (makes no API calls):

```bash
fly ssh console -u appuser -C "ghdcbot --config /app/config/config.yaml preflight-sync"
```

It prints `Sync preflight OK` when the config loads, `GITHUB_TOKEN` / `DISCORD_TOKEN` resolve, and the sync safety guard passes. A missing secret fails with `Missing required environment variable`. It does not call Discord or GitHub; a bad token shows up in `fly logs` when the bot or first sync runs.

Keep `runtime.mode: "dry-run"` until you have reviewed `/data/reports/audit.md` (see [Audit-first workflow](DOCKER.md#audit-first-workflow-in-docker)).

---

## How it stays reliable

- **Volume permissions:** the image's entrypoint starts as root, `chown`s `/data` to `appuser` if needed, then drops privileges with `setpriv`. Gitcord itself never runs as root.
- **Supervision:** `fly-supervisor.sh` watches the bot and the sync loop. If either exits, it stops the other and exits non-zero, and `[[restart]] policy = "always"` brings the machine back. You will not end up with a bot that is online while syncs have silently stopped.
- **No overlapping syncs:** the scheduled loop (`scripts/sync-loop.sh`) and the mentor `/sync` command take the same `flock` on `/data/run-once.lock` (override with `GITCORD_SYNC_LOCK_FILE`). If one is running, the scheduled run is skipped, and `/sync` replies that a sync is already running instead of starting a second one. Only one machine ever uses the volume.

---

## Configuration

| Setting | Where | Default |
| ------- | ----- | ------- |
| Sync interval (seconds) | `[env] GITCORD_SYNC_INTERVAL_SECONDS` in `fly.toml` | `21600` (6 h) |
| Config path | `[env] GITCORD_CONFIG` | `/app/config/config.yaml` |
| Machine size | `[[vm]]` in `fly.toml` | `shared-cpu-1x`, 512 MB |

After changing `config/config.yaml` or `fly.toml`, run `fly deploy --ha=false` again. State in `/data` is kept across deploys.

---

## Updating

```bash
git pull
fly deploy --ha=false
```

Back up the volume before risky changes:

```bash
fly volumes snapshots create <volume-id>
```

Find the id with `fly volumes list`.

---

## Troubleshooting

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `fly deploy` fails on `config/config.yaml` | Local file missing (it is referenced by `[[files]]`). | Create it from `config/docker-example.yaml` (step 1). |
| `fly-start: config file not found` in logs | Config was not shipped to the machine. | Redeploy after creating `config/config.yaml`. |
| `Missing required environment variable: GITHUB_TOKEN` | Secrets not set. | `fly secrets set GITHUB_TOKEN=... DISCORD_TOKEN=...` |
| Machine restarts in a loop | The bot or sync loop is crashing; the supervisor exits on purpose. | `fly logs` shows which one (`fly-supervisor: ... exited unexpectedly`). Run the `preflight-sync` check shown above. |
| State lost after redeploy | Volume not attached, or `data_dir` is not `/data`. | `fly volumes list`, and keep `data_dir: "/data"` in config. |
| Volume not found on deploy | Volume region differs from `primary_region`. | Create the volume in the same region. |

---

## Not covered

- **GitHub App auth** (`GITHUB_APP_*`): the PAT path above is what this guide supports. App auth needs the private key available as a file in the machine, which this setup does not provision.
- **Running more than one org:** deploy a separate Fly app (own name, volume, secrets, and config) per org. Never share a volume or tokens between orgs.

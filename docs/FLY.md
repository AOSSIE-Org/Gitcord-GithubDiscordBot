# Fly.io Deployment Guide

Gitcord provides first-class support for deployment on [Fly.io](https://fly.io), allowing you to run the bot and its background sync loop efficiently on a single VM.

## Overview

- **No Inbound Web Traffic**: Gitcord acts as a Discord bot and polls GitHub; no inbound HTTP services are configured or needed.
- **Persistent Volume**: A single Fly volume (`gitcord_data`) is mounted at `/data` to persist SQLite state, verified identities, and sync cursors across restarts and redeploys.
- **Secure Secrets**: `GITHUB_TOKEN` and `DISCORD_TOKEN` are provided securely via Fly secrets rather than a `.env` file.
- **Single Container Wrapper**: A lightweight wrapper script (`scripts/fly-start.sh`) ensures both the Discord bot and the periodic sync loop run safely without overlapping syncs.

## Prerequisites

1. Install the [Fly.io CLI (`flyctl`)](https://fly.io/docs/hands-on/install-flyctl/).
2. Run `fly auth login` to authenticate.
3. Setup `config/config.yaml` locally with your org settings (`github.org` and `discord.guild_id`) as described in the [Installation Guide](../INSTALLATION.md). **Ensure `runtime.data_dir: "/data"` is set.**

## Step-by-Step Deployment

### 1. Initialize the App

Run `fly launch` in the root of the repository. When prompted:

- **Do you want to tweak these settings before proceeding?** Press **y** (yes) if you want to name the app.
- Name your app (e.g., `gitcord-myorg`).
- Choose your preferred region.
- **Database**: Do **not** add Postgres or Redis (Gitcord uses its own local SQLite).
- Do **not** deploy immediately if asked. We need to create the volume and add secrets first!

The local `fly.toml` is already pre-configured to build from our `Dockerfile`, map the volume, and use our custom `fly-start.sh` script.

### 2. Create the Persistent Volume

Gitcord relies on SQLite to store identity links and sync cursors. We must create the volume defined in `fly.toml` (`gitcord_data`) before deploying:

```bash
fly volumes create gitcord_data --region <your-region> --size 1
```

*(You can check your configured region by looking at `primary_region` in your generated `fly.toml` or running `fly status`.)*

### 3. Set the Secrets

Inject your GitHub and Discord tokens directly into the app's secure secrets vault:

```bash
fly secrets set GITHUB_TOKEN="your_github_token" DISCORD_TOKEN="your_discord_token"
```

*Note: Never commit your `.env` file or hardcode tokens in `config/config.yaml`.*

### 4. Deploy

Now you are ready to bring up the bot:

```bash
fly deploy --ha=false
```

We pass `--ha=false` to ensure Fly only spins up a **single machine** instead of the default two. Gitcord requires a single machine to prevent overlapping SQLite writes and sync jobs. 

You can follow the live logs to ensure both the bot and sync loop have started:

```bash
fly logs
```

## Scaling Constraints

Gitcord is designed to run its background sync loop on a **single machine**. Our Fly deployment enforces this by bundling both processes into the same container (`scripts/fly-start.sh`). 

**Do not scale up to multiple machines.** The SQLite database requires exclusive write access, and a single block volume cannot be actively mounted to multiple machines simultaneously. If you ever need to verify the machine count:

```bash
fly scale count 1
```

## Updating

When a new version of Gitcord is available or when you update your `config/config.yaml`:

```bash
git pull
fly deploy
```

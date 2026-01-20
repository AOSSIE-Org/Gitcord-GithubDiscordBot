from __future__ import annotations

import argparse
import logging

from ghdcbot.config.loader import load_config
from ghdcbot.core.errors import AdapterError, ConfigError
from ghdcbot.engine.orchestrator import Orchestrator
from ghdcbot.logging.setup import configure_logging
from ghdcbot.plugins.registry import build_adapter


def build_orchestrator(config_path: str) -> Orchestrator:
    config = load_config(config_path)
    configure_logging(config.runtime.log_level)
    logger = logging.getLogger("CLI")
    logger.info("Loaded configuration", extra={"mode": config.runtime.mode.value})

    github_adapter = build_adapter(
        config.runtime.github_adapter,
        token=config.github.token,
        org=config.github.org,
        api_base=str(config.github.api_base),
    )
    discord_adapter = build_adapter(
        config.runtime.discord_adapter,
        token=config.discord.token,
        guild_id=config.discord.guild_id,
    )
    storage_adapter = build_adapter(
        config.runtime.storage_adapter,
        data_dir=config.runtime.data_dir,
    )

    return Orchestrator(
        github_reader=github_adapter,
        github_writer=github_adapter,
        discord_reader=discord_adapter,
        discord_writer=discord_adapter,
        storage=storage_adapter,
        config=config,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Discord-GitHub automation engine")
    parser.add_argument("--config", required=True, help="Path to config YAML file")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run-once", help="Run a single orchestration cycle")

    args = parser.parse_args()
    orchestrator = None
    try:
        orchestrator = build_orchestrator(args.config)
        if args.command == "run-once":
            orchestrator.run_once()
    except (ConfigError, AdapterError) as exc:
        logging.getLogger("CLI").error("Fatal error: %s", exc)
        raise SystemExit(1) from exc
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("CLI").exception("Unhandled error")
        raise SystemExit(1) from exc
    finally:
        if orchestrator is not None:
            orchestrator.close()


if __name__ == "__main__":
    main()

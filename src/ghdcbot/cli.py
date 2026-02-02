from __future__ import annotations

import argparse
import logging

from ghdcbot.config.loader import load_config
from ghdcbot.core.errors import AdapterError, ConfigError
from ghdcbot.adapters.github.identity import GitHubIdentityReader
from ghdcbot.engine.identity_linking import IdentityLinkService
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


def _build_identity_service(
    config_path: str,
) -> tuple[IdentityLinkService, Orchestrator, GitHubIdentityReader]:
    """Build an IdentityLinkService using the same config + storage.

    Returns the service and an orchestrator for cleanup (close()).
    """
    orchestrator = build_orchestrator(config_path)
    github_identity = GitHubIdentityReader(
        token=orchestrator.config.github.token,
        api_base=str(orchestrator.config.github.api_base),
    )
    service = IdentityLinkService(storage=orchestrator.storage, github_identity=github_identity)
    return service, orchestrator, github_identity


def main() -> None:
    parser = argparse.ArgumentParser(description="Discord-GitHub automation engine")
    parser.add_argument("--config", required=True, help="Path to config YAML file")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run-once", help="Run a single orchestration cycle")
    link_p = sub.add_parser("link", help="Create a GitHub identity link claim (phase-1 verification)")
    link_p.add_argument("--discord-user-id", required=True, help="Discord user ID (numeric)")
    link_p.add_argument("github_user", help="GitHub username to claim")
    verify_p = sub.add_parser("verify-link", help="Verify a pending identity claim")
    verify_p.add_argument("--discord-user-id", required=True, help="Discord user ID (numeric)")
    verify_p.add_argument("github_user", help="GitHub username to verify")
    sub.add_parser("bot", help="Run Discord bot with /link and /verify-link slash commands")

    args = parser.parse_args()
    orchestrator = None
    try:
        identity_reader = None
        if args.command == "run-once":
            orchestrator = build_orchestrator(args.config)
            orchestrator.run_once()
        elif args.command == "bot":
            from ghdcbot.bot import main as bot_main
            bot_main(args.config)
        elif args.command in {"link", "verify-link"}:
            config = load_config(args.config)
            configure_logging(config.runtime.log_level)
            service, orchestrator, identity_reader = _build_identity_service(args.config)
            if args.command == "link":
                claim = service.create_claim(args.discord_user_id, args.github_user)
                logging.getLogger("CLI").info(
                    "Link claim created",
                    extra={
                        "discord_user_id": claim.discord_user_id,
                        "github_user": claim.github_user,
                        "expires_at": claim.expires_at.isoformat(),
                    },
                )
                print(
                    "\n".join(
                        [
                            "Verification steps:",
                            f"1) Put this code in your GitHub bio OR in a public GitHub gist: {claim.verification_code}",
                            "2) Re-run verification:",
                            f"   ghdcbot --config {args.config} verify-link --discord-user-id {args.discord_user_id} {args.github_user}",
                            f"Expires at (UTC): {claim.expires_at.isoformat()}",
                        ]
                    )
                )
            else:
                ok, location = service.verify_claim(args.discord_user_id, args.github_user)
                if ok:
                    print(f"Verified: {args.github_user} ↔ {args.discord_user_id} (found in {location})")
                else:
                    if location == "expired":
                        print("Verification code expired. Run link again to generate a new code.")
                    else:
                        print("Not verified yet. Add the code to your bio or a public gist and try again.")
    except (ConfigError, AdapterError) as exc:
        logging.getLogger("CLI").error("Fatal error: %s", exc)
        raise SystemExit(1) from exc
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("CLI").exception("Unhandled error")
        raise SystemExit(1) from exc
    finally:
        if orchestrator is not None:
            orchestrator.close()
        if "identity_reader" in locals() and identity_reader is not None:
            close = getattr(identity_reader, "close", None)
            if callable(close):
                close()


if __name__ == "__main__":
    main()

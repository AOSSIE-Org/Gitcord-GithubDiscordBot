from __future__ import annotations

import argparse
import csv
import io
import json
import logging
from pathlib import Path

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
    unlink_p = sub.add_parser("unlink", help="Unlink your verified GitHub identity (Discord-initiated, cooldown applies)")
    unlink_p.add_argument("--discord-user-id", required=True, help="Discord user ID (numeric)")
    identity_p = sub.add_parser("identity", help="Identity status (read-only)")
    identity_sub = identity_p.add_subparsers(dest="identity_command", required=True)
    identity_status_p = identity_sub.add_parser("status", help="Show linked GitHub account and verification status")
    identity_status_p.add_argument("--discord-user-id", required=True, help="Discord user ID (numeric)")
    identity_sub.add_parser("list", help="List all verified contributors (Discord ID ↔ GitHub username)")
    sub.add_parser("bot", help="Run Discord bot with /link and /verify-link slash commands")
    export_p = sub.add_parser("export-audit", help="Export append-only audit events (JSON, CSV, or Markdown)")
    export_p.add_argument("--format", choices=("json", "csv", "md"), default="json", help="Output format")
    export_p.add_argument("--output", type=str, default=None, help="Output file (default: stdout)")
    export_p.add_argument("--user", type=str, default=None, help="Filter by GitHub user or Discord user ID")
    export_p.add_argument("--event-type", type=str, default=None, help="Filter by event type (e.g. identity_verified)")
    export_p.add_argument("--from", dest="from_time", type=str, default=None, help="Filter from time (ISO-8601 UTC)")
    export_p.add_argument("--to", dest="to_time", type=str, default=None, help="Filter to time (ISO-8601 UTC)")

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
        elif args.command == "export-audit":
            from datetime import datetime as dt
            from ghdcbot.engine.audit_export import (
                filter_audit_events,
                format_audit_csv,
                format_audit_markdown,
            )
            config = load_config(args.config)
            configure_logging(config.runtime.log_level)
            # Read audit events (reuse storage method if available, else read file directly)
            storage_adapter = build_adapter(
                config.runtime.storage_adapter,
                data_dir=config.runtime.data_dir,
            )
            list_events = getattr(storage_adapter, "list_audit_events", None)
            if callable(list_events):
                events = list_events()
            else:
                # Fallback to direct file read (backward compatible)
                path = Path(config.runtime.data_dir) / "audit_events.jsonl"
                events = []
                if path.exists():
                    with path.open(encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    events.append(json.loads(line))
                                except json.JSONDecodeError:
                                    continue
            # Parse time filters
            from_time = None
            to_time = None
            if args.from_time:
                try:
                    from_time = dt.fromisoformat(args.from_time.replace("Z", "+00:00"))
                except ValueError as e:
                    logging.getLogger("CLI").error("Invalid --from time format (use ISO-8601 UTC): %s", e)
                    raise SystemExit(1) from e
            if args.to_time:
                try:
                    to_time = dt.fromisoformat(args.to_time.replace("Z", "+00:00"))
                except ValueError as e:
                    logging.getLogger("CLI").error("Invalid --to time format (use ISO-8601 UTC): %s", e)
                    raise SystemExit(1) from e
            # Apply filters
            filtered = filter_audit_events(
                events,
                user=args.user,
                event_type=args.event_type,
                from_time=from_time,
                to_time=to_time,
            )
            # Format output
            if args.format == "json":
                out = json.dumps(filtered, indent=2)
            elif args.format == "csv":
                out = format_audit_csv(filtered)
            else:  # md
                out = format_audit_markdown(filtered)
            if args.output:
                Path(args.output).write_text(out, encoding="utf-8")
            else:
                print(out)
        elif args.command == "identity":
            config = load_config(args.config)
            configure_logging(config.runtime.log_level)
            storage_adapter = build_adapter(
                config.runtime.storage_adapter,
                data_dir=config.runtime.data_dir,
            )
            storage_adapter.init_schema()
            if args.identity_command == "list":
                list_verified = getattr(storage_adapter, "list_verified_identity_mappings", None)
                if not callable(list_verified):
                    logging.getLogger("CLI").error("identity list is not available for this storage")
                    raise SystemExit(1)
                mappings = list_verified()
                if not mappings:
                    print("No verified contributors yet.")
                else:
                    print(f"Verified contributors ({len(mappings)}):")
                    for m in mappings:
                        print(f"  Discord: {m.discord_user_id}  ↔  GitHub: {m.github_user}")
            elif args.identity_command == "status":
                get_status = getattr(storage_adapter, "get_identity_status", None)
                if not callable(get_status):
                    logging.getLogger("CLI").error("identity status is not available")
                    raise SystemExit(1)
                max_age_days = None
                if config.identity is not None:
                    max_age_days = getattr(config.identity, "verified_max_age_days", None)
                status = get_status(args.discord_user_id, max_age_days=max_age_days)
                github_user = status.get("github_user") or "-"
                st = status.get("status") or "not_linked"
                if st == "verified":
                    status_label = "Verified"
                elif st == "verified_stale":
                    status_label = "Verified (Stale)"
                elif st == "pending":
                    status_label = "Pending"
                else:
                    status_label = "Not linked"
                verified_at = status.get("verified_at") or "-"
                print(f"Discord user: {args.discord_user_id}")
                print(f"GitHub user: {github_user}")
                print(f"Status: {status_label}")
                print(f"Verified at: {verified_at}")
                if status.get("is_stale"):
                    print("Warning: Identity verification is stale. Use verify-link to refresh it.")
            else:
                logging.getLogger("CLI").error("Unknown identity command: %s", args.identity_command)
                raise SystemExit(1)
        elif args.command == "unlink":
            config = load_config(args.config)
            configure_logging(config.runtime.log_level)
            service, orchestrator, identity_reader = _build_identity_service(args.config)
            cooldown = (config.identity.unlink_cooldown_hours if config.identity else 24)
            try:
                service.unlink(args.discord_user_id, cooldown)
                print("Unlinked. You can use /link or the link command again to relink.")
            except ValueError as e:
                print(str(e))
        elif args.command in {"link", "verify-link"}:
            config = load_config(args.config)
            configure_logging(config.runtime.log_level)
            service, orchestrator, identity_reader = _build_identity_service(args.config)
            if args.command == "link":
                max_age_days = None
                if config.identity is not None:
                    max_age_days = getattr(config.identity, "verified_max_age_days", None)
                claim = service.create_claim(args.discord_user_id, args.github_user, max_age_days=max_age_days)
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

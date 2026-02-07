"""Discord bot for identity linking and informational slash commands."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands

from ghdcbot.adapters.github.identity import GitHubIdentityReader
from ghdcbot.config.loader import load_config
from ghdcbot.core.errors import ConfigError
from ghdcbot.engine.identity_linking import IdentityLinkService
from ghdcbot.engine.metrics import (
    format_metrics_summary,
    get_contribution_metrics,
    get_rank_for_user,
    rank_by_activity,
)
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.issue_assignment import (
    build_assignment_confirmation_embed,
    fetch_issue_context,
    get_assignee_activity,
    parse_issue_url,
    resolve_discord_to_github,
    resolve_github_to_discord,
)
from ghdcbot.engine.issue_request_flow import (
    build_mentor_request_embed,
    build_repo_selection_embed,
    compute_eligibility,
    get_merged_pr_count_and_last_time,
    group_pending_requests_by_repo,
)
from ghdcbot.engine.pr_context import (
    build_pr_embed,
    fetch_pr_context,
    parse_pr_url,
)
from ghdcbot.logging.setup import configure_logging
from ghdcbot.plugins.registry import build_adapter


def run_bot(config_path: str) -> None:
    """Run the Discord bot with /link, /verify-link, /verify, /status, and /summary."""
    config = load_config(config_path)
    configure_logging(config.runtime.log_level)
    logger = logging.getLogger("ghdcbot.bot")
    logger.info(
        "Using config: %s → data_dir: %s (identity links persist here)",
        config_path,
        config.runtime.data_dir,
    )

    storage = build_adapter(
        config.runtime.storage_adapter,
        data_dir=config.runtime.data_dir,
    )
    storage.init_schema()
    github_identity = GitHubIdentityReader(
        token=config.github.token,
        api_base=str(config.github.api_base),
    )
    service = IdentityLinkService(storage=storage, github_identity=github_identity)
    discord_reader = build_adapter(
        config.runtime.discord_adapter,
        token=config.discord.token,
        guild_id=config.discord.guild_id,
    )
    github_adapter = build_adapter(
        config.runtime.github_adapter,
        token=config.github.token,
        org=config.github.org,
        api_base=str(config.github.api_base),
    )

    intents = discord.Intents.default()
    # Enable message content intent if passive PR preview is enabled
    if getattr(config.discord, "pr_preview_channels", None):
        intents.message_content = True
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)
    guild_id = int(config.discord.guild_id)

    @tree.command(
        name="link",
        description="Link your Discord account to a GitHub account (you get a verification code)",
        guild=discord.Object(id=guild_id),
    )
    @app_commands.describe(github_username="Your GitHub username")
    async def link_cmd(interaction: discord.Interaction, github_username: str) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        max_age_days = None
        if getattr(config, "identity", None) is not None:
            max_age_days = getattr(config.identity, "verified_max_age_days", None)
        try:
            claim = service.create_claim(discord_user_id, github_username, max_age_days=max_age_days)
        except ValueError as e:
            await interaction.followup.send(
                f"Cannot create link: {e}",
                ephemeral=True,
            )
            return
        msg = (
            f"**Verification code:** `{claim.verification_code}`\n\n"
            "1. Put this code in your **GitHub profile bio** or in a **public gist**.\n"
            f"2. Run `/verify-link` with `{github_username}` here.\n\n"
            f"Code expires at (UTC): {claim.expires_at.isoformat()}"
        )
        await interaction.followup.send(msg, ephemeral=True)

    @tree.command(
        name="verify-link",
        description="Verify your GitHub link after adding the code to your bio or a gist",
        guild=discord.Object(id=guild_id),
    )
    @app_commands.describe(github_username="Your GitHub username")
    async def verify_link_cmd(interaction: discord.Interaction, github_username: str) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        try:
            ok, location = service.verify_claim(discord_user_id, github_username)
        except ValueError as e:
            await interaction.followup.send(
                f"Verification failed: {e}",
                ephemeral=True,
            )
            return
        if ok:
            if location == "already-verified":
                await interaction.followup.send(
                    f"Your account is already linked to **{github_username}**.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"Verified: **{github_username}** ↔ your Discord (found in {location}).",
                    ephemeral=True,
                )
        else:
            if location == "expired":
                await interaction.followup.send(
                    "Verification code expired. Run `/link` again to get a new code.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Code not found yet. Add the code to your GitHub bio or a public gist, then run `/verify-link` again.",
                    ephemeral=True,
                )

    @tree.command(
        name="verify",
        description="Show your GitHub link verification status (read-only)",
        guild=discord.Object(id=guild_id),
    )
    async def verify_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        get_links = getattr(storage, "get_identity_links_for_discord_user", None)
        if callable(get_links):
            links = get_links(discord_user_id)
        else:
            links = []
            verified = getattr(storage, "list_verified_identity_mappings", None)
            if callable(verified):
                for m in verified():
                    if m.discord_user_id == discord_user_id:
                        links.append({"github_user": m.github_user, "verified": 1})
                        break
        if not links:
            await interaction.followup.send(
                "Not linked. Use `/link` to start.",
                ephemeral=True,
            )
            return
        verified_row = next((r for r in links if int(r.get("verified") or 0) == 1), None)
        pending = [r for r in links if int(r.get("verified") or 0) == 0]
        if verified_row:
            msg = f"Linked to GitHub: **{verified_row.get('github_user', '?')}**."
            # Check for stale status
            get_status = getattr(storage, "get_identity_status", None)
            if callable(get_status):
                max_age_days = None
                if getattr(config, "identity", None) is not None:
                    max_age_days = getattr(config.identity, "verified_max_age_days", None)
                status = get_status(discord_user_id, max_age_days=max_age_days)
                if status.get("is_stale"):
                    msg += "\n\n⚠️ **Warning:** Your identity verification is stale. Use `/verify-link` to refresh it."
            await interaction.followup.send(msg, ephemeral=True)
        elif pending:
            p = pending[0]
            exp = p.get("expires_at") or "—"
            await interaction.followup.send(
                f"Pending: link to **{p.get('github_user', '?')}** (expires: {exp}). Run `/verify-link` to complete.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "Not linked. Use `/link` to start.",
                ephemeral=True,
            )

    @tree.command(
        name="status",
        description="Show verification state, activity window, and your roles (read-only)",
        guild=discord.Object(id=guild_id),
    )
    async def status_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        period_days = config.scoring.period_days
        lines = [f"**Activity window:** last {period_days} days (from bot config)."]
        get_links = getattr(storage, "get_identity_links_for_discord_user", None)
        if callable(get_links):
            links = get_links(discord_user_id)
            verified_row = next((r for r in links if int(r.get("verified") or 0) == 1), None)
            if verified_row:
                lines.append(f"**Linked GitHub:** {verified_row.get('github_user', '?')}.")
                # Check for stale status
                get_status = getattr(storage, "get_identity_status", None)
                if callable(get_status):
                    max_age_days = None
                    if getattr(config, "identity", None) is not None:
                        max_age_days = getattr(config.identity, "verified_max_age_days", None)
                    status = get_status(discord_user_id, max_age_days=max_age_days)
                    if status.get("is_stale"):
                        lines.append("⚠️ **Warning:** Identity verification is stale. Use `/verify-link` to refresh.")
            else:
                lines.append("**Linked GitHub:** not linked.")
        else:
            lines.append("**Linked GitHub:** (link status unavailable).")
        member_roles = discord_reader.list_member_roles()
        my_roles = member_roles.get(discord_user_id, [])
        if my_roles:
            lines.append(f"**Your roles:** {', '.join(my_roles)}.")
        else:
            lines.append("**Your roles:** (none or unable to read).")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @tree.command(
        name="summary",
        description="Show your contribution metrics summary (last 7 and 30 days; read-only)",
        guild=discord.Object(id=guild_id),
    )
    async def summary_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        get_links = getattr(storage, "get_identity_links_for_discord_user", None)
        if not callable(get_links):
            await interaction.followup.send(
                "Link status unavailable. Use `/link` to link your GitHub account.",
                ephemeral=True,
            )
            return
        links = get_links(discord_user_id)
        verified_row = next((r for r in links if int(r.get("verified") or 0) == 1), None)
        if not verified_row:
            await interaction.followup.send(
                "Link your account with `/link` and `/verify-link` to see your summary.",
                ephemeral=True,
            )
            return
        github_user = verified_row.get("github_user", "")
        if not github_user:
            await interaction.followup.send("Linked user unknown.", ephemeral=True)
            return
        # Check for stale status
        stale_warning = ""
        get_status = getattr(storage, "get_identity_status", None)
        if callable(get_status):
            max_age_days = None
            if getattr(config, "identity", None) is not None:
                max_age_days = getattr(config.identity, "verified_max_age_days", None)
            status = get_status(discord_user_id, max_age_days=max_age_days)
            if status.get("is_stale"):
                stale_warning = "\n\n⚠️ **Warning:** Identity verification is stale. Use `/verify-link` to refresh it."
        now = datetime.now(timezone.utc)
        weights = getattr(config.scoring, "weights", None) or {}
        parts = []
        for days in (7, 30):
            start = now - timedelta(days=days)
            metrics_list = get_contribution_metrics(storage, start, now, weights)
            user_metrics = next((m for m in metrics_list if m.github_user == github_user), None)
            parts.append(f"**Last {days} days:**\n{format_metrics_summary(user_metrics)}")
        ranked_30 = rank_by_activity(
            get_contribution_metrics(storage, now - timedelta(days=30), now, weights)
        )
        rank = get_rank_for_user(ranked_30, github_user)
        if rank is not None:
            parts.append(f"Top contributors by activity (last 30 days): you're #{rank}.")
        await interaction.followup.send("\n\n".join(parts) + stale_warning, ephemeral=True)

    identity_group = app_commands.Group(
        name="identity",
        description="Identity linking status (read-only)",
    )

    @identity_group.command(
        name="status",
        description="Show your linked GitHub account and verification status (read-only)",
    )
    async def identity_status_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        get_status = getattr(storage, "get_identity_status", None)
        if not callable(get_status):
            await interaction.followup.send(
                "Identity status is unavailable.",
                ephemeral=True,
            )
            return
        max_age_days = None
        if getattr(config, "identity", None) is not None:
            max_age_days = getattr(config.identity, "verified_max_age_days", None)
        status = get_status(discord_user_id, max_age_days=max_age_days)
        github_user = status.get("github_user") or "—"
        st = status.get("status") or "not_linked"
        if st == "verified":
            status_label = "Verified ✅"
        elif st == "verified_stale":
            status_label = "Verified ⚠️ (Stale)"
        elif st == "pending":
            status_label = "Pending ⏳"
        else:
            status_label = "Not linked ❌"
        verified_at = status.get("verified_at")
        verified_at_str = verified_at if verified_at else "—"
        if verified_at_str != "—":
            try:
                dt = datetime.fromisoformat(verified_at_str.replace("Z", "+00:00"))
                verified_at_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            except (ValueError, TypeError):
                pass
        msg = (
            f"**GitHub user:** {github_user}\n"
            f"**Status:** {status_label}\n"
            f"**Verified at:** {verified_at_str}"
        )
        if status.get("is_stale"):
            msg += "\n\n⚠️ **Warning:** Your identity verification is stale. Use `/verify-link` to refresh it."
        await interaction.followup.send(msg, ephemeral=True)

    tree.add_command(identity_group, guild=discord.Object(id=guild_id))

    @tree.command(
        name="unlink",
        description="Unlink your verified GitHub identity (cooldown applies after verification)",
        guild=discord.Object(id=guild_id),
    )
    async def unlink_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        cooldown = 24
        if getattr(config, "identity", None) is not None:
            cooldown = getattr(config.identity, "unlink_cooldown_hours", 24) or 24
        try:
            service.unlink(discord_user_id, cooldown)
            await interaction.followup.send(
                "Identity unlinked. You can use `/link` again to relink.",
                ephemeral=True,
            )
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)

    @tree.command(
        name="pr-info",
        description="Show PR context preview (repository, status, reviews, CI, mentor signal)",
        guild=discord.Object(id=guild_id),
    )
    @app_commands.describe(pr_url="GitHub Pull Request URL")
    async def pr_info_cmd(interaction: discord.Interaction, pr_url: str) -> None:
        await interaction.response.defer(ephemeral=False)
        
        # Parse PR URL
        parsed = parse_pr_url(pr_url)
        if not parsed:
            await interaction.followup.send("Invalid GitHub PR URL", ephemeral=True)
            return
        
        owner, repo, pr_number = parsed
        
        # Fetch PR context
        try:
            pr, reviews, ci_status, last_commit_time = fetch_pr_context(
                github_adapter, owner, repo, pr_number
            )
        except Exception as exc:
            logger.exception("Failed to fetch PR context", extra={"owner": owner, "repo": repo, "pr_number": pr_number})
            await interaction.followup.send(
                f"Error fetching PR: {exc}",
                ephemeral=True,
            )
            return
        
        if not pr:
            await interaction.followup.send(
                "PR not accessible with current token",
                ephemeral=True,
            )
            return
        
        # Get Discord mention if author is linked
        author_github = pr.get("user", {}).get("login", "")
        discord_mention = None
        if author_github:
            get_links = getattr(storage, "get_identity_links_for_discord_user", None)
            if callable(get_links):
                # Search verified mappings for this GitHub user
                verified = getattr(storage, "list_verified_identity_mappings", None)
                if callable(verified):
                    for mapping in verified():
                        if mapping.github_user == author_github:
                            discord_mention = f"<@{mapping.discord_user_id}>"
                            break
        
        # Build embed
        embed_dict = build_pr_embed(
            pr=pr,
            owner=owner,
            repo=repo,
            reviews=reviews,
            ci_status=ci_status,
            last_commit_time=last_commit_time,
            discord_mention=discord_mention,
        )
        
        embed = discord.Embed.from_dict(embed_dict)
        await interaction.followup.send(embed=embed, ephemeral=False)

    # Issue assignment confirmation view
    class IssueAssignmentView(discord.ui.View):
        """View with buttons for confirming issue assignment."""
        
        def __init__(
            self,
            owner: str,
            repo: str,
            issue_number: int,
            new_assignee_github: str,
            new_assignee_discord: str | None,
            has_existing_assignee: bool,
            github_adapter: Any,
            storage: Any,
            policy: MutationPolicy,
            timeout: float = 300.0,  # 5 minutes
        ) -> None:
            super().__init__(timeout=timeout)
            self.owner = owner
            self.repo = repo
            self.issue_number = issue_number
            self.new_assignee_github = new_assignee_github
            self.new_assignee_discord = new_assignee_discord
            self.has_existing_assignee = has_existing_assignee
            self.github_adapter = github_adapter
            self.storage = storage
            self.policy = policy
        
        async def on_timeout(self) -> None:
            """Handle view timeout."""
            for item in self.children:
                item.disabled = True
            if hasattr(self, "message") and self.message:
                try:
                    await self.message.edit(view=self)
                except discord.NotFound:
                    pass
        
        @discord.ui.button(label="Confirm Assignment", style=discord.ButtonStyle.success, emoji="✅")
        async def confirm_assignment(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            """Handle confirm assignment button."""
            await interaction.response.defer(ephemeral=True)
            
            # Re-check issue state (TOCTOU protection)
            issue = fetch_issue_context(self.github_adapter, self.owner, self.repo, self.issue_number)
            if not issue:
                await interaction.followup.send(
                    "❌ Issue not found or inaccessible. Assignment cancelled.",
                    ephemeral=True,
                )
                return
            
            if issue.get("state", "").lower() == "closed":
                await interaction.followup.send(
                    "❌ Issue is closed. Assignment cancelled.",
                    ephemeral=True,
                )
                return
            
            # Check if still allowed
            if not self.policy.allow_github_mutations:
                skip_reason = "dry-run" if self.policy.mode == RunMode.DRY_RUN else "observer mode" if self.policy.mode == RunMode.OBSERVER else "write disabled"
                await interaction.followup.send(
                    f"❌ Assignment skipped ({skip_reason}). No changes made.",
                    ephemeral=True,
                )
                # Log audit event
                if self.storage and hasattr(self.storage, "append_audit_event"):
                    self.storage.append_audit_event({
                        "event_type": "issue_assignment_cancelled",
                        "context": {
                            "reason": skip_reason,
                            "issue": f"{self.owner}/{self.repo}#{self.issue_number}",
                            "proposed_assignee": self.new_assignee_github,
                            "actor_discord_id": str(interaction.user.id),
                        },
                    })
                return
            
            # Perform assignment
            success = self.github_adapter.assign_issue(
                self.owner, self.repo, self.issue_number, self.new_assignee_github
            )
            
            if success:
                # Log audit event
                if self.storage and hasattr(self.storage, "append_audit_event"):
                    self.storage.append_audit_event({
                        "event_type": "issue_assigned_from_discord",
                        "context": {
                            "actor_discord_id": str(interaction.user.id),
                            "issue": f"{self.owner}/{self.repo}#{self.issue_number}",
                            "new_assignee": self.new_assignee_github,
                            "replaced": self.has_existing_assignee,
                        },
                    })
                
                await interaction.followup.send(
                    "✅ Issue assigned successfully!",
                    ephemeral=True,
                )
                
                # Update original message
                if hasattr(self, "message") and self.message:
                    try:
                        embed_dict = self.message.embeds[0].to_dict() if self.message.embeds else {}
                        embed_dict["color"] = 0x10B981  # Green for success
                        embed_dict["title"] = "✅ Issue Assigned"
                        embed = discord.Embed.from_dict(embed_dict)
                        await self.message.edit(embed=embed, view=None)
                    except Exception:
                        pass
            else:
                await interaction.followup.send(
                    "❌ Failed to assign issue. Please try again or check GitHub permissions.",
                    ephemeral=True,
                )
        
        @discord.ui.button(label="Replace Assignee", style=discord.ButtonStyle.primary, emoji="🔁")
        async def replace_assignee(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            """Handle replace assignee button."""
            await interaction.response.defer(ephemeral=True)
            
            # Re-check issue state (TOCTOU protection)
            issue = fetch_issue_context(self.github_adapter, self.owner, self.repo, self.issue_number)
            if not issue:
                await interaction.followup.send(
                    "❌ Issue not found or inaccessible. Assignment cancelled.",
                    ephemeral=True,
                )
                return
            
            if issue.get("state", "").lower() == "closed":
                await interaction.followup.send(
                    "❌ Issue is closed. Assignment cancelled.",
                    ephemeral=True,
                )
                return
            
            # Get current assignee
            assignees = issue.get("assignees", [])
            if not assignees:
                await interaction.followup.send(
                    "❌ Issue has no current assignee. Use 'Confirm Assignment' instead.",
                    ephemeral=True,
                )
                return
            
            old_assignee = assignees[0].get("login", "")
            
            # Check if still allowed
            if not self.policy.allow_github_mutations:
                skip_reason = "dry-run" if self.policy.mode == RunMode.DRY_RUN else "observer mode" if self.policy.mode == RunMode.OBSERVER else "write disabled"
                await interaction.followup.send(
                    f"❌ Assignment skipped ({skip_reason}). No changes made.",
                    ephemeral=True,
                )
                # Log audit event
                if self.storage and hasattr(self.storage, "append_audit_event"):
                    self.storage.append_audit_event({
                        "event_type": "issue_assignment_cancelled",
                        "context": {
                            "reason": skip_reason,
                            "issue": f"{self.owner}/{self.repo}#{self.issue_number}",
                            "proposed_assignee": self.new_assignee_github,
                            "actor_discord_id": str(interaction.user.id),
                        },
                    })
                return
            
            # Unassign old assignee and assign new one
            unassign_success = self.github_adapter.unassign_issue(
                self.owner, self.repo, self.issue_number, old_assignee
            )
            assign_success = self.github_adapter.assign_issue(
                self.owner, self.repo, self.issue_number, self.new_assignee_github
            )
            
            if unassign_success and assign_success:
                # Log audit event
                if self.storage and hasattr(self.storage, "append_audit_event"):
                    self.storage.append_audit_event({
                        "event_type": "issue_reassigned_from_discord",
                        "context": {
                            "actor_discord_id": str(interaction.user.id),
                            "issue": f"{self.owner}/{self.repo}#{self.issue_number}",
                            "old_assignee": old_assignee,
                            "new_assignee": self.new_assignee_github,
                        },
                    })
                
                await interaction.followup.send(
                    "🔁 Issue reassigned successfully!",
                    ephemeral=True,
                )
                
                # Update original message
                if hasattr(self, "message") and self.message:
                    try:
                        embed_dict = self.message.embeds[0].to_dict() if self.message.embeds else {}
                        embed_dict["color"] = 0x10B981  # Green for success
                        embed_dict["title"] = "🔁 Issue Reassigned"
                        embed = discord.Embed.from_dict(embed_dict)
                        await self.message.edit(embed=embed, view=None)
                    except Exception:
                        pass
            else:
                await interaction.followup.send(
                    "❌ Failed to reassign issue. Please try again or check GitHub permissions.",
                    ephemeral=True,
                )
        
        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌")
        async def cancel_assignment(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            """Handle cancel button."""
            await interaction.response.defer(ephemeral=True)
            
            # Log audit event
            if self.storage and hasattr(self.storage, "append_audit_event"):
                self.storage.append_audit_event({
                    "event_type": "issue_assignment_cancelled",
                    "context": {
                        "issue": f"{self.owner}/{self.repo}#{self.issue_number}",
                        "proposed_assignee": self.new_assignee_github,
                        "actor_discord_id": str(interaction.user.id),
                    },
                })
            
            await interaction.followup.send(
                "❌ Assignment cancelled. No changes made.",
                ephemeral=True,
            )
            
            # Update original message
            if hasattr(self, "message") and self.message:
                try:
                    embed_dict = self.message.embeds[0].to_dict() if self.message.embeds else {}
                    embed_dict["color"] = 0xEF4444  # Red for cancelled
                    embed_dict["title"] = "❌ Assignment Cancelled"
                    embed = discord.Embed.from_dict(embed_dict)
                    await self.message.edit(embed=embed, view=None)
                except Exception:
                    pass

    @tree.command(
        name="assign-issue",
        description="Assign a GitHub issue to a Discord user (mentor-only, requires confirmation)",
        guild=discord.Object(id=guild_id),
    )
    @app_commands.describe(
        issue_url="GitHub issue URL (e.g., https://github.com/owner/repo/issues/123)",
        assignee="Discord user to assign the issue to"
    )
    async def assign_issue_cmd(
        interaction: discord.Interaction,
        issue_url: str,
        assignee: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=False)
        
        # Check mentor role
        mentor_roles = getattr(config, "assignments", None)
        if mentor_roles:
            issue_assignee_roles = getattr(mentor_roles, "issue_assignees", [])
            user_roles = [role.name for role in interaction.user.roles]
            if not any(role in issue_assignee_roles for role in user_roles):
                await interaction.followup.send(
                    f"❌ Permission denied. Only mentors with roles {', '.join(issue_assignee_roles)} can assign issues.",
                    ephemeral=True,
                )
                return
        
        # Parse issue URL
        parsed = parse_issue_url(issue_url)
        if not parsed:
            await interaction.followup.send(
                "❌ Invalid GitHub issue URL. Format: https://github.com/owner/repo/issues/123",
                ephemeral=True,
            )
            return
        
        owner, repo, issue_number = parsed
        
        # Fetch issue context
        try:
            issue = fetch_issue_context(github_adapter, owner, repo, issue_number)
        except Exception as exc:
            logger.exception("Failed to fetch issue context", extra={"owner": owner, "repo": repo, "issue_number": issue_number})
            await interaction.followup.send(
                f"❌ Error fetching issue: {exc}",
                ephemeral=True,
            )
            return
        
        if not issue:
            await interaction.followup.send(
                "❌ Issue not accessible with current token.",
                ephemeral=True,
            )
            return
        
        # Check if issue is closed
        if issue.get("state", "").lower() == "closed":
            await interaction.followup.send(
                "❌ Cannot assign closed issues.",
                ephemeral=True,
            )
            return
        
        # Resolve Discord user to GitHub username
        assignee_discord_id = str(assignee.id)
        assignee_github = resolve_discord_to_github(storage, assignee_discord_id)
        
        if not assignee_github:
            await interaction.followup.send(
                f"❌ {assignee.mention} has not verified their GitHub identity. Use `/link` and `/verify-link` first.",
                ephemeral=True,
            )
            return
        
        # Check current assignment
        assignees = issue.get("assignees", [])
        current_assignee_github = None
        current_assignee_discord = None
        assignee_activity = "Unknown"
        has_existing_assignee = False
        
        if assignees:
            has_existing_assignee = True
            current_assignee_github = assignees[0].get("login", "")
            current_assignee_discord = resolve_github_to_discord(storage, current_assignee_github)
            # Get activity (simplified for now)
            assignee_activity = "Unknown"
        
        # Build confirmation embed
        now = datetime.now(timezone.utc)
        embed_dict = build_assignment_confirmation_embed(
            issue=issue,
            owner=owner,
            repo=repo,
            current_assignee_github=current_assignee_github,
            current_assignee_discord=current_assignee_discord,
            new_assignee_github=assignee_github,
            new_assignee_discord=assignee_discord_id,
            assignee_activity=assignee_activity,
            now=now,
        )
        
        embed = discord.Embed.from_dict(embed_dict)
        
        # Create view with buttons
        policy = MutationPolicy(
            mode=config.runtime.mode,
            github_write_allowed=config.github.permissions.write,
            discord_write_allowed=config.discord.permissions.write,
        )
        
        view = IssueAssignmentView(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            new_assignee_github=assignee_github,
            new_assignee_discord=assignee_discord_id,
            has_existing_assignee=has_existing_assignee,
            github_adapter=github_adapter,
            storage=storage,
            policy=policy,
        )
        
        # Show appropriate buttons based on assignment state
        if has_existing_assignee:
            # Hide confirm button, show replace button
            view.confirm_assignment.disabled = True
            view.confirm_assignment.style = discord.ButtonStyle.secondary
        else:
            # Hide replace button, show confirm button
            view.replace_assignee.disabled = True
            view.replace_assignee.style = discord.ButtonStyle.secondary
        
        # Send confirmation message
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=False)
        view.message = message

    # -------- Issue request flow: contributor requests, mentor reviews --------

    def _request_created_at(req: dict) -> datetime:
        """Parse created_at from request dict for sorting (oldest first)."""
        v = req.get("created_at")
        if v is None:
            return datetime.max.replace(tzinfo=timezone.utc)
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return datetime.max.replace(tzinfo=timezone.utc)

    class RepoSelectView(discord.ui.View):
        """Step 1: Mentor selects a repository to see pending issue requests."""

        def __init__(
            self,
            pending_requests: list,
            repo_list: list,
            storage: Any,
            github_adapter: Any,
            config: Any,
            discord_reader: Any,
            policy: MutationPolicy,
            timeout: float = 300.0,
        ) -> None:
            super().__init__(timeout=timeout)
            self.pending_requests = pending_requests
            self.repo_list = repo_list
            self.storage = storage
            self.github_adapter = github_adapter
            self.config = config
            self.discord_reader = discord_reader
            self.policy = policy
            options = [
                discord.SelectOption(
                    label=f"{r['owner']}/{r['repo']}"[:100],
                    value=f"{r['owner']}/{r['repo']}",
                    description=f"{r['count']} request(s)",
                )
                for r in repo_list[:25]
            ]
            if options:
                select = discord.ui.Select(
                    placeholder="Choose a repository",
                    options=options,
                    custom_id="repo_select",
                )
                select.callback = self._on_select_callback
                self.add_item(select)

        async def _on_select_callback(self, interaction: discord.Interaction) -> None:
            if not interaction.data or "values" not in interaction.data or not interaction.data["values"]:
                await interaction.response.defer(ephemeral=True)
                await interaction.followup.send("No repository selected.", ephemeral=True)
                return
            await self._on_repo_chosen(interaction, interaction.data["values"][0])

        async def _on_repo_chosen(self, interaction: discord.Interaction, repo_value: str) -> None:
            await interaction.response.defer(ephemeral=False)
            parts = repo_value.split("/", 1)
            if len(parts) != 2:
                await interaction.followup.send("Invalid repository selection.", ephemeral=True)
                return
            owner, repo = parts[0], parts[1]
            repo_requests = [
                r for r in self.pending_requests
                if r.get("owner") == owner and r.get("repo") == repo
            ]
            repo_requests.sort(key=lambda r: (_request_created_at(r), r.get("request_id", "")))
            if hasattr(self.storage, "append_audit_event"):
                self.storage.append_audit_event({
                    "event_type": "issue_request_viewed_repo",
                    "context": {
                        "repo": repo_value,
                        "mentor_discord_id": str(interaction.user.id),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                })
            period_days = self.config.scoring.period_days
            period_end = datetime.now(timezone.utc)
            period_start = period_end - timedelta(days=period_days)
            mentor_roles = getattr(self.config, "assignments", None)
            eligible_roles_config = getattr(mentor_roles, "issue_request_eligible_roles", []) if mentor_roles else []
            member_roles_map = self.discord_reader.list_member_roles()
            channel = interaction.channel

            async def send_repo_list_back(ch: Any) -> None:
                pending = self.storage.list_pending_issue_requests()
                if not pending:
                    await ch.send("No pending issue requests.")
                    return
                rl = group_pending_requests_by_repo(pending)
                now = datetime.now(timezone.utc)
                emb = discord.Embed.from_dict(build_repo_selection_embed(rl, now))
                v = RepoSelectView(
                    pending, rl, self.storage, self.github_adapter, self.config,
                    self.discord_reader, self.policy,
                )
                await ch.send(embed=emb, view=v)

            for req in repo_requests:
                issue = fetch_issue_context(
                    self.github_adapter, req["owner"], req["repo"], req["issue_number"]
                )
                if not issue:
                    continue
                contributor_roles = member_roles_map.get(req["discord_user_id"], [])
                merged_count, last_merged_at = get_merged_pr_count_and_last_time(
                    self.storage, req["github_user"], period_start, period_end
                )
                now = datetime.now(timezone.utc)
                verdict, reason = compute_eligibility(
                    eligible_roles_config, contributor_roles, merged_count, last_merged_at, now
                )
                embed_dict = build_mentor_request_embed(
                    request=req,
                    issue=issue,
                    contributor_discord_mention=f"<@{req['discord_user_id']}>",
                    contributor_roles=contributor_roles,
                    merged_count=merged_count,
                    last_merged_at=last_merged_at,
                    eligibility_verdict=verdict,
                    eligibility_reason=reason,
                    eligible_roles_config=eligible_roles_config,
                    period_days=period_days,
                    now=now,
                )
                view = IssueRequestReviewView(
                    request_id=req["request_id"],
                    owner=req["owner"],
                    repo=req["repo"],
                    issue_number=req["issue_number"],
                    requester_github=req["github_user"],
                    requester_discord_id=req["discord_user_id"],
                    github_adapter=self.github_adapter,
                    storage=self.storage,
                    policy=self.policy,
                    discord_sender=self.discord_reader,
                    back_callback=send_repo_list_back,
                )
                msg = await channel.send(embed=discord.Embed.from_dict(embed_dict), view=view)
                view.message = msg
            await interaction.followup.send(
                f"Showing **{len(repo_requests)}** request(s) for **{repo_value}** above.",
                ephemeral=False,
            )

    class IssueRequestReviewView(discord.ui.View):
        """Mentor review: Approve, Replace, Reject, or Cancel for an issue request."""

        def __init__(
            self,
            request_id: str,
            owner: str,
            repo: str,
            issue_number: int,
            requester_github: str,
            requester_discord_id: str,
            github_adapter: Any,
            storage: Any,
            policy: MutationPolicy,
            discord_sender: Any,
            back_callback: Any = None,
            timeout: float = 300.0,
        ) -> None:
            super().__init__(timeout=timeout)
            self.request_id = request_id
            self.owner = owner
            self.repo = repo
            self.issue_number = issue_number
            self.requester_github = requester_github
            self.requester_discord_id = requester_discord_id
            self.github_adapter = github_adapter
            self.storage = storage
            self.policy = policy
            self.discord_sender = discord_sender
            self.back_callback = back_callback
            if back_callback:
                back_btn = discord.ui.Button(
                    label="Back to Repo List",
                    style=discord.ButtonStyle.secondary,
                    emoji="⬅️",
                )
                back_btn.callback = self._back_to_repo_list
                self.add_item(back_btn)

        async def _back_to_repo_list(self, interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True)
            if self.back_callback and interaction.channel:
                await self.back_callback(interaction.channel)
            await interaction.followup.send("Returned to repo list.", ephemeral=True)

        def _dm_contributor(self, content: str) -> bool:
            send_dm = getattr(self.discord_sender, "send_dm", None)
            if not callable(send_dm):
                return False
            return send_dm(self.requester_discord_id, content)

        async def _revalidate_and_assign(self, interaction: discord.Interaction, replace: bool) -> bool:
            """Re-fetch issue, re-validate, then assign. Returns True if assignment was done."""
            req = getattr(self.storage, "get_issue_request", None) and self.storage.get_issue_request(self.request_id)
            if not req or req.get("status") != "pending":
                await interaction.followup.send("❌ Request no longer pending or not found.", ephemeral=True)
                return False
            issue = fetch_issue_context(self.github_adapter, self.owner, self.repo, self.issue_number)
            if not issue:
                await interaction.followup.send("❌ Issue not found or inaccessible.", ephemeral=True)
                return False
            if issue.get("state", "").lower() == "closed":
                await interaction.followup.send("❌ Issue is closed. Request cancelled.", ephemeral=True)
                return False
            if not self.policy.allow_github_mutations:
                await interaction.followup.send(
                    "❌ GitHub writes are disabled (dry-run/observer or write disabled).",
                    ephemeral=True,
                )
                return False
            assignees = issue.get("assignees", [])
            if replace:
                if not assignees:
                    await interaction.followup.send("❌ No existing assignee to replace.", ephemeral=True)
                    return False
                old = assignees[0].get("login", "")
                if not self.github_adapter.unassign_issue(self.owner, self.repo, self.issue_number, old):
                    await interaction.followup.send("❌ Failed to unassign current assignee.", ephemeral=True)
                    return False
            if not self.github_adapter.assign_issue(self.owner, self.repo, self.issue_number, self.requester_github):
                await interaction.followup.send("❌ Failed to assign issue. Check GitHub permissions.", ephemeral=True)
                return False
            return True

        @discord.ui.button(label="Approve & Assign", style=discord.ButtonStyle.success, emoji="✅")
        async def approve_assign(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            await interaction.response.defer(ephemeral=True)
            if not await self._revalidate_and_assign(interaction, replace=False):
                return
            self.storage.update_issue_request_status(self.request_id, "approved")
            if hasattr(self.storage, "append_audit_event"):
                self.storage.append_audit_event({
                    "event_type": "issue_request_approved",
                    "context": {
                        "request_id": self.request_id,
                        "repo": f"{self.owner}/{self.repo}",
                        "issue_number": self.issue_number,
                        "mentor_discord_id": str(interaction.user.id),
                        "contributor_discord_id": self.requester_discord_id,
                        "assignee": self.requester_github,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                })
            self._dm_contributor(
                f"✅ Your request to be assigned to {self.owner}/{self.repo}#{self.issue_number} was approved. You’re assigned!"
            )
            await interaction.followup.send("✅ Request approved and issue assigned.", ephemeral=True)
            if hasattr(self, "message") and self.message:
                try:
                    embed_dict = self.message.embeds[0].to_dict() if self.message.embeds else {}
                    embed_dict["color"] = 0x10B981
                    embed_dict["title"] = "✅ Approved & assigned"
                    await self.message.edit(embed=discord.Embed.from_dict(embed_dict), view=None)
                except Exception:
                    pass

        @discord.ui.button(label="Replace Existing Assignee", style=discord.ButtonStyle.primary, emoji="🔁")
        async def replace_assignee(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            await interaction.response.defer(ephemeral=True)
            if not await self._revalidate_and_assign(interaction, replace=True):
                return
            self.storage.update_issue_request_status(self.request_id, "approved")
            if hasattr(self.storage, "append_audit_event"):
                self.storage.append_audit_event({
                    "event_type": "issue_request_reassigned",
                    "context": {
                        "request_id": self.request_id,
                        "repo": f"{self.owner}/{self.repo}",
                        "issue_number": self.issue_number,
                        "mentor_discord_id": str(interaction.user.id),
                        "contributor_discord_id": self.requester_discord_id,
                        "new_assignee": self.requester_github,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                })
            self._dm_contributor(
                f"✅ Your request for {self.owner}/{self.repo}#{self.issue_number} was approved. You’re now assigned (previous assignee was replaced)."
            )
            await interaction.followup.send("🔁 Replaced assignee and assigned contributor.", ephemeral=True)
            if hasattr(self, "message") and self.message:
                try:
                    embed_dict = self.message.embeds[0].to_dict() if self.message.embeds else {}
                    embed_dict["color"] = 0x10B981
                    embed_dict["title"] = "🔁 Reassigned"
                    await self.message.edit(embed=discord.Embed.from_dict(embed_dict), view=None)
                except Exception:
                    pass

        @discord.ui.button(label="Reject Request", style=discord.ButtonStyle.danger, emoji="❌")
        async def reject_request(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            await interaction.response.defer(ephemeral=True)
            req = getattr(self.storage, "get_issue_request", None) and self.storage.get_issue_request(self.request_id)
            if not req or req.get("status") != "pending":
                await interaction.followup.send("❌ Request no longer pending or not found.", ephemeral=True)
                return
            self.storage.update_issue_request_status(self.request_id, "rejected")
            if hasattr(self.storage, "append_audit_event"):
                self.storage.append_audit_event({
                    "event_type": "issue_request_rejected",
                    "context": {
                        "request_id": self.request_id,
                        "repo": f"{self.owner}/{self.repo}",
                        "issue_number": self.issue_number,
                        "mentor_discord_id": str(interaction.user.id),
                        "contributor_discord_id": self.requester_discord_id,
                        "requester": self.requester_github,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                })
            self._dm_contributor(
                f"Your request to work on {self.owner}/{self.repo}#{self.issue_number} was declined. You can ask a mentor for feedback or pick another issue."
            )
            await interaction.followup.send("❌ Request rejected; contributor DM’d.", ephemeral=True)
            if hasattr(self, "message") and self.message:
                try:
                    embed_dict = self.message.embeds[0].to_dict() if self.message.embeds else {}
                    embed_dict["color"] = 0xEF4444
                    embed_dict["title"] = "❌ Rejected"
                    await self.message.edit(embed=discord.Embed.from_dict(embed_dict), view=None)
                except Exception:
                    pass

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="🚫")
        async def cancel_action(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send("No action taken.", ephemeral=True)

    @tree.command(
        name="request-issue",
        description="Request to be assigned to a GitHub issue (contributor)",
        guild=discord.Object(id=guild_id),
    )
    @app_commands.describe(issue_url="GitHub issue URL")
    async def request_issue_cmd(interaction: discord.Interaction, issue_url: str) -> None:
        await interaction.response.defer(ephemeral=True)
        parsed = parse_issue_url(issue_url)
        if not parsed:
            await interaction.followup.send(
                "❌ Invalid GitHub issue URL. Use format: https://github.com/owner/repo/issues/123",
                ephemeral=True,
            )
            return
        owner, repo, issue_number = parsed
        if owner != config.github.org:
            await interaction.followup.send(
                f"❌ Issue must be in the configured organization ({config.github.org}).",
                ephemeral=True,
            )
            return
        issue = fetch_issue_context(github_adapter, owner, repo, issue_number)
        if not issue:
            await interaction.followup.send("❌ Issue not found or inaccessible.", ephemeral=True)
            return
        if issue.get("state", "").lower() == "closed":
            await interaction.followup.send("❌ Cannot request assignment to a closed issue.", ephemeral=True)
            return
        discord_user_id = str(interaction.user.id)
        github_user = resolve_discord_to_github(storage, discord_user_id)
        if not github_user:
            await interaction.followup.send(
                "❌ You must link your GitHub account first. Use `/link` and `/verify-link`.",
                ephemeral=True,
            )
            return
        request_id = str(uuid.uuid4())
        issue_url_clean = issue.get("html_url", f"https://github.com/{owner}/{repo}/issues/{issue_number}")
        if hasattr(storage, "insert_issue_request"):
            storage.insert_issue_request(
                request_id, discord_user_id, github_user, owner, repo, issue_number, issue_url_clean
            )
        if hasattr(storage, "append_audit_event"):
            storage.append_audit_event({
                "event_type": "issue_request_created",
                "context": {
                    "request_id": request_id,
                    "discord_user_id": discord_user_id,
                    "github_user": github_user,
                    "issue": f"{owner}/{repo}#{issue_number}",
                },
            })
        await interaction.followup.send(
            f"✅ Request recorded. Mentors will review and decide on assignment for **{owner}/{repo}#{issue_number}**.",
            ephemeral=True,
        )

    @tree.command(
        name="issue-requests",
        description="List pending issue assignment requests (mentor-only); pick a repo first.",
        guild=discord.Object(id=guild_id),
    )
    async def issue_requests_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=False)
        mentor_roles = getattr(config, "assignments", None)
        issue_assignee_roles = getattr(mentor_roles, "issue_assignees", []) if mentor_roles else []
        user_roles = [r.name for r in interaction.user.roles]
        if not any(r in issue_assignee_roles for r in user_roles):
            await interaction.followup.send(
                f"❌ Only mentors ({', '.join(issue_assignee_roles) or 'configure issue_assignees'}) can review requests.",
                ephemeral=True,
            )
            return
        pending = getattr(storage, "list_pending_issue_requests", None)
        if not callable(pending):
            await interaction.followup.send("❌ Request list unavailable.", ephemeral=True)
            return
        requests_list = pending()
        if not requests_list:
            await interaction.followup.send("No pending issue requests.", ephemeral=True)
            return
        repo_list = group_pending_requests_by_repo(requests_list)
        now = datetime.now(timezone.utc)
        embed_dict = build_repo_selection_embed(repo_list, now)
        policy = MutationPolicy(
            mode=config.runtime.mode,
            github_write_allowed=config.github.permissions.write,
            discord_write_allowed=config.discord.permissions.write,
        )
        view = RepoSelectView(
            requests_list,
            repo_list,
            storage,
            github_adapter,
            config,
            discord_reader,
            policy,
        )
        await interaction.followup.send(
            embed=discord.Embed.from_dict(embed_dict),
            view=view,
            ephemeral=False,
        )

    @client.event
    async def on_message(message: discord.Message) -> None:
        """Handle passive PR URL detection in configured channels."""
        # Ignore bot messages
        if message.author.bot:
            return
        
        # Check if passive detection is enabled
        pr_preview_channels = getattr(config.discord, "pr_preview_channels", None)
        if not pr_preview_channels:
            return
        
        # Check if message is in a configured channel
        channel_name = message.channel.name if hasattr(message.channel, "name") else None
        if channel_name not in pr_preview_channels:
            return
        
        # Look for PR URLs in message content
        content = message.content or ""
        parsed = parse_pr_url(content)
        if not parsed:
            return
        
        owner, repo, pr_number = parsed
        
        # Fetch and send PR preview
        try:
            pr, reviews, ci_status, last_commit_time = fetch_pr_context(
                github_adapter, owner, repo, pr_number
            )
        except Exception:
            logger.exception(
                "Failed to fetch PR context from message",
                extra={"owner": owner, "repo": repo, "pr_number": pr_number},
            )
            return
        
        if not pr:
            # Silently fail for passive detection
            return
        
        # Get Discord mention if author is linked
        author_github = pr.get("user", {}).get("login", "")
        discord_mention = None
        if author_github:
            verified = getattr(storage, "list_verified_identity_mappings", None)
            if callable(verified):
                for mapping in verified():
                    if mapping.github_user == author_github:
                        discord_mention = f"<@{mapping.discord_user_id}>"
                        break
        
        # Build and send embed
        embed_dict = build_pr_embed(
            pr=pr,
            owner=owner,
            repo=repo,
            reviews=reviews,
            ci_status=ci_status,
            last_commit_time=last_commit_time,
            discord_mention=discord_mention,
        )
        
        embed = discord.Embed.from_dict(embed_dict)
        await message.channel.send(embed=embed)

    @client.event
    async def on_ready() -> None:
        synced = await tree.sync(guild=discord.Object(id=guild_id))
        cmd_names = [c.name for c in synced]
        logger.info("Bot ready; slash commands synced for guild %s: %s", guild_id, cmd_names)

    client.run(config.discord.token)


def main(config_path: str) -> None:
    """Entry point for running the bot (handles ConfigError)."""
    try:
        run_bot(config_path)
    except ConfigError as e:
        logging.getLogger("ghdcbot.bot").error("Config error: %s", e)
        raise SystemExit(1) from e

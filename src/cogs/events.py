import discord
from discord.ext import commands, tasks
import yaml
import os
import random
import logging
from database import (get_repos, update_repo_etag, update_score, 
                      get_discord_from_github, get_maintainers_for_repo,
                      mark_event_processed, is_event_processed)
from github_client import GitHubClient

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../../config.yaml')
try:
    with open(CONFIG_PATH, 'r') as f:
        config = yaml.safe_load(f)
except:
    config = {'scoring': {'points': {}}}

class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.gh_client = GitHubClient(config['github']['token'], config['github']['organization'])
        self.sync_events.start()

    def cog_unload(self):
        self.sync_events.cancel()

    @tasks.loop(minutes=2)
    async def sync_events(self):
        repos = get_repos()
        for repo_row in repos:
            repo_url = repo_row['repo_url']
            repo_id = repo_row['id']
            channel_id = repo_row['channel_id']
            etag = repo_row['last_event_etag']
            owner = repo_row['owner']
            name = repo_row['name']

            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue

            events, new_etag = self.gh_client.get_repo_events(owner, name, etag)
            
            if not events:
                continue
            
            # Process oldest first (reverse of API response) to maintain narrative flow
            for event in reversed(events):
                if is_event_processed(event['id']):
                    continue
                
                try:
                    await self.process_event(channel, event, repo_url)
                    mark_event_processed(event['id'])
                except Exception as e:
                    print(f"Error processing event {event['id']}: {e}")
            
            if new_etag:
                update_repo_etag(repo_id, new_etag)

    async def _get_random_maintainer(self, repo_url, exclude_id=None):
        maintainers = get_maintainers_for_repo(repo_url)
        candidates = [m for m in maintainers if m != exclude_id] if exclude_id else maintainers
        if candidates:
            return f"<@{random.choice(candidates)}>"
        return "maintainers"

    async def process_event(self, channel, event, repo_url):
        etype = event['type']
        payload = event['payload']
        actor_gh = event['actor']['login']
        
        # Helper: Get Discord Identity
        def resolve_user(gh_user):
            row = get_discord_from_github(gh_user)
            if row:
                return f"<@{row['discord_id']}>", row['discord_id'], row['score'], True
            return gh_user, None, 0, False

        actor_mention, actor_id, _, actor_mapped = resolve_user(actor_gh)
        points_conf = config['scoring']['points']

        if etype == 'IssuesEvent':
            action = payload['action']
            issue = payload['issue']
            issue_url = issue['html_url']
            
            if action == 'assigned':
                assignee_gh = payload.get('assignee', {}).get('login')
                if assignee_gh:
                    u_mention, u_id, _, u_mapped = resolve_user(assignee_gh)
                    if u_mapped:
                        pts = points_conf.get('issue_assigned', 0)
                        update_score(u_id, pts)
                        await channel.send(f"📋 Issue {issue_url} assigned to {u_mention} (+{pts} points)")

            elif action == 'opened':
                # Check if creator is maintainer
                maintainers = get_maintainers_for_repo(repo_url)
                is_maintainer = actor_id in maintainers if actor_id else False
                
                if is_maintainer and actor_mapped:
                    await channel.send(f"📢 Issue available for assignment {issue_url} by {actor_mention}")
                elif actor_mapped:
                    # Random maintainer assignment request
                    mnt_mention = await self._get_random_maintainer(repo_url, exclude_id=actor_id)
                    await channel.send(f"🐛 Issue created {issue_url} by {actor_mention}. {mnt_mention} please assign.")

        elif etype == 'PullRequestEvent':
            action = payload['action']
            pr = payload['pull_request']
            pr_url = pr['html_url']
            
            if action == 'opened' and actor_mapped:
                mnt_mention = await self._get_random_maintainer(repo_url, exclude_id=actor_id)
                await channel.send(f"🔌 PR opened {pr_url} by {actor_mention}. {mnt_mention} please review.")
            
            elif action == 'closed':
                if pr.get('merged', False) and actor_mapped:
                    pts = points_conf.get('pr_merged', 10)
                    update_score(actor_id, pts)
                    await channel.send(f"💜 PR merged! {pr_url} from {actor_mention} (+{pts} points)")
                elif not pr.get('merged', False) and actor_mapped:
                    await channel.send(f"❌ PR closed without merge {pr_url} from {actor_mention}")

        elif etype == 'PullRequestReviewEvent':
            action = payload['action']
            review = payload['review']
            pr = payload['pull_request']
            
            creator_gh = pr['user']['login']
            creator_mention, creator_id, _, creator_mapped = resolve_user(creator_gh)

            # Check if Reviewer (actor) is maintainer
            maintainers = get_maintainers_for_repo(repo_url)
            is_reviewer_maintainer = actor_id in maintainers if actor_id else False

            if action == 'submitted' and is_reviewer_maintainer and creator_mapped:
                pts = points_conf.get('pr_reviewed', 5)
                update_score(creator_id, pts)
                
                comment_preview = review.get('body') or "No comment."
                if len(comment_preview) > 50: comment_preview = comment_preview[:47] + "..."
                
                await channel.send(f"👀 PR reviewed {pr['html_url']} from {creator_mention} (+{pts} points). Review: {comment_preview}")

    @sync_events.before_loop
    async def before_sync(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(Events(bot))

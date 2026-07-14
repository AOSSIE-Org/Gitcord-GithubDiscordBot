# Identity Verification: `/link` and `/verify-link`

This document explains the Gitcord identity verification system end to end: what happens when a Discord user runs `/link`, how `/verify-link` proves GitHub ownership, where the data is stored, and which code paths implement the flow.

## What This System Solves

Gitcord needs a trusted mapping between a Discord user and a GitHub username. That mapping is used later for contribution summaries, role planning, issue assignment, PR context mentions, and other features that depend on knowing which GitHub account belongs to which Discord member.

The project uses a lightweight verification-code flow instead of OAuth:

1. A Discord user runs `/link github_username`.
2. Gitcord creates a short-lived verification code and stores a pending claim.
3. The user puts that code in their GitHub profile bio or a public gist.
4. The user clicks the **Verify** button in the ephemeral `/link` message.
5. Gitcord checks public GitHub data for the code.
6. If the code is found, Gitcord marks the Discord-to-GitHub mapping as verified.

The older `/verify-link github_username` command remains available and uses the same verification logic.

## Main Files

| File | Purpose |
| --- | --- |
| `src/ghdcbot/bot.py` | Discord slash commands and identity verification buttons: `/link`, `/verify-link`, `/profile`, `/unlink`. |
| `src/ghdcbot/engine/identity_linking.py` | Business logic for creating claims, verifying claims, generating codes, audit events, and unlinking. |
| `src/ghdcbot/adapters/github/identity.py` | Read-only GitHub adapter that searches the user's bio and public gists for the code. |
| `src/ghdcbot/adapters/storage/sqlite.py` | SQLite schema and persistence for pending and verified identity links. |
| `src/ghdcbot/cli.py` | CLI version of the same `link` and `verify-link` workflow. |
| `tests/test_identity_linking.py` | Tests covering code generation, verification, impersonation protection, stale refresh, unlinking, and status. |

## High-Level Flow

```text
Discord user
  |
  | /link github_username
  v
bot.py link_cmd()
  |
  v
IdentityLinkService.create_claim()
  |
  | generate 10-character code
  | create pending identity_links row
  | write identity_claim_created audit event
  v
User puts code in GitHub bio or public gist
  |
  | click Verify
  v
bot.py IdentityVerificationView.verify_identity()
  |
  v
IdentityLinkService.verify_claim()
  |
  | load pending claim from SQLite
  | reject expired claim
  | search GitHub bio and public gists
  v
GitHubIdentityReader.search_verification_code()
  |
  v
If found:
  mark row verified=1
  clear verification_code and expires_at
  set verified_at
  write identity_verified audit event
```

## Discord Command Setup

When the bot starts, it builds shared storage, GitHub identity reader, and identity service instances:

```python
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
```

This means the Discord bot and CLI both use the same SQLite storage and the same verification service.

## `/link`: Create a Pending Claim and Button UI

The `/link` slash command lives in `src/ghdcbot/bot.py`.

```python
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
    embed = build_identity_verification_embed(claim)
    view = IdentityVerificationView(
        service=service,
        storage=storage,
        discord_user_id=discord_user_id,
        github_user=github_username,
    )
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
```

Important behavior:

- The reply is ephemeral, so only the user sees the verification code and buttons.
- The Discord user ID comes from `interaction.user.id`; users cannot provide someone else's Discord ID through the slash command.
- The command calls `IdentityLinkService.create_claim()`.
- If a duplicate, impersonation, or already-verified case is detected, the command sends the `ValueError` message back to the user.
- The Verify button calls the same `IdentityLinkService.verify_claim()` method used by `/verify-link`.

The embed is built from the stored claim:

```python
def build_identity_verification_embed(claim: LinkClaim) -> discord.Embed:
    embed = discord.Embed(
        title="Verify GitHub Account",
        description=(
            "1. Copy this code.\n"
            "2. Paste it into your GitHub bio or public gist.\n"
            "3. Click Verify."
        ),
        color=0x2563EB,
    )
    embed.add_field(name="GitHub Account", value=claim.github_user, inline=False)
    embed.add_field(name="Verification Code", value=f"`{claim.verification_code}`", inline=False)
    embed.add_field(name="Expires At (UTC)", value=claim.expires_at.isoformat(), inline=False)
    return embed
```

## Verify and Cancel Buttons

`IdentityVerificationView` owns the two buttons shown under the `/link` embed:

```python
verify_button = discord.ui.Button(label="Verify", style=discord.ButtonStyle.success)
verify_button.callback = self.verify_identity
self.add_item(verify_button)

cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
cancel_button.callback = self.cancel_verification
self.add_item(cancel_button)
```

The Verify button:

1. Uses `interaction.user.id` to identify the Discord user who clicked.
2. Loads the saved identity row from SQLite.
3. Reads the GitHub username from that row.
4. Calls `IdentityLinkService.verify_claim(discord_user_id, github_user)`.

It does not duplicate GitHub lookup, expiry handling, storage updates, or audit behavior.

If the code is not found, the button sends an ephemeral failure message and leaves the original Verify button active so the user can fix their bio/gist and try again.

The Cancel button edits the original ephemeral message to:

```text
Verification cancelled.
```

and disables the buttons.

## Claim Creation Logic

The service creates a `LinkClaim` with a generated code and expiration time:

```python
@dataclass(frozen=True)
class LinkClaim:
    discord_user_id: str
    github_user: str
    verification_code: str
    expires_at: datetime
```

```python
def create_claim(self, discord_user_id: str, github_user: str, *, max_age_days: int | None = None) -> LinkClaim:
    code = _generate_verification_code()
    expires_at = datetime.now(timezone.utc) + self._ttl
    self._storage.create_identity_claim(
        discord_user_id=discord_user_id,
        github_user=github_user,
        verification_code=code,
        expires_at=expires_at,
        max_age_days=max_age_days,
    )
    append_audit = getattr(self._storage, "append_audit_event", None)
    if callable(append_audit):
        append_audit({
            "actor_type": "discord_user",
            "actor_id": discord_user_id,
            "event_type": "identity_claim_created",
            "context": {"github_user": github_user, "expires_at": expires_at.isoformat()},
        })
    return LinkClaim(
        discord_user_id=discord_user_id,
        github_user=github_user,
        verification_code=code,
        expires_at=expires_at,
    )
```

The verification code is 10 characters long and uses uppercase letters plus digits:

```python
def _generate_verification_code(length: int = 10) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))
```

By default, claims live for 10 minutes because `IdentityLinkService` sets `ttl_minutes: int = 10`.

## SQLite Storage

Identity links are stored in the `identity_links` table:

```sql
CREATE TABLE IF NOT EXISTS identity_links (
    discord_user_id TEXT NOT NULL,
    github_user TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    verification_code TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    verified_at TEXT,
    PRIMARY KEY (discord_user_id, github_user)
);
```

The schema also adds:

- `unlinked_at TEXT`, so unlinking preserves history instead of deleting rows.
- `github_user_normalized TEXT`, so GitHub username comparisons are case-insensitive.
- A unique index on `(discord_user_id, github_user_normalized)`.
- Indexes for `github_user` and `verified`.

`create_identity_claim()` protects against impersonation and accidental duplicate linking:

```python
def create_identity_claim(
    self,
    discord_user_id: str,
    github_user: str,
    verification_code: str,
    expires_at: datetime,
    *,
    max_age_days: int | None = None,
) -> None:
    """Create or refresh an identity claim for (discord_user_id, github_user).

    Impersonation protection:
    - If github_user is already verified for a different discord_user_id, reject.
    - If an unexpired claim exists for github_user under a different discord_user_id, reject.
    - If an expired claim exists for github_user under a different discord_user_id, replace it.

    Stale refresh:
    - If already verified for same pair and stale (per max_age_days), allow creating new claim to refresh.
    """
```

Key storage rules:

- One GitHub username cannot be verified by two Discord users.
- One active pending GitHub username claim cannot be held by two Discord users.
- One Discord user cannot verify two different GitHub users.
- Expired pending claims from other users can be cleaned up and replaced.
- A verified mapping can be refreshed only when it is stale according to `identity.verified_max_age_days`.

## `/verify-link`: Backward-Compatible Manual Verification

The `/verify-link` slash command still lives in `src/ghdcbot/bot.py` and remains fully functional.

```python
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
                f"Verified: **{github_username}** <-> your Discord (found in {location}).",
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
```

Possible outcomes:

| Result | Meaning | User message |
| --- | --- | --- |
| `True, "already-verified"` | The mapping was already verified. | Already linked. |
| `True, "bio"` | The code was found in the GitHub profile bio. | Verified. |
| `True, "gist:<id>:description"` | The code was found in a public gist description. | Verified. |
| `True, "gist:<id>:<filename>"` | The code was found in a public gist file. | Verified. |
| `False, "expired"` | The pending claim expired. | Run `/link` again. |
| `False, None` | The code was not found yet. | Add the code and retry. |
| `ValueError` | There is no matching claim or the claim data is invalid. | Verification failed. |

## Verification Logic

`IdentityLinkService.verify_claim()` is the core of `/verify-link`:

```python
def verify_claim(self, discord_user_id: str, github_user: str) -> tuple[bool, str | None]:
    row = self._storage.get_identity_link(discord_user_id, github_user)
    if not row:
        raise ValueError("No identity claim found for this Discord user and GitHub user")
    if int(row.get("verified") or 0) == 1:
        return True, "already-verified"

    code = row.get("verification_code")
    expires_at_raw = row.get("expires_at")
    if not code or not expires_at_raw:
        raise ValueError("Identity claim is missing verification_code or expires_at")

    expires_at = datetime.fromisoformat(expires_at_raw)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    expires_at = expires_at.astimezone(timezone.utc)

    now = datetime.now(timezone.utc)
    if expires_at <= now:
        return False, "expired"

    match: VerificationMatch = self._github.search_verification_code(github_user, code)
    if not match.found:
        return False, None

    self._storage.mark_identity_verified(discord_user_id, github_user)
    return True, match.location
```

The full implementation also writes audit events for these cases:

- `identity_verification_expired`
- `identity_verification_not_found`
- `identity_verified`

## GitHub Code Search

`GitHubIdentityReader` is intentionally read-only. It does not decide whether a claim should be accepted; it only fetches public GitHub data and searches for the code.

```python
@dataclass(frozen=True)
class VerificationMatch:
    found: bool
    location: str | None = None
```

```python
def search_verification_code(self, github_user: str, code: str) -> VerificationMatch:
    """Search for code in GitHub bio or public gists."""
    bio = self._fetch_bio(github_user)
    if bio and code in bio:
        return VerificationMatch(found=True, location="bio")

    for match in self._search_public_gists(github_user, code):
        return match

    return VerificationMatch(found=False, location=None)
```

The lookup order is:

1. `GET /users/{github_user}` and check the `bio` field.
2. `GET /users/{github_user}/gists?per_page=20&page=1`.
3. Check each gist description.
4. Fetch gist details with `GET /gists/{gist_id}`.
5. Fetch each file `raw_url` and search the raw text for the code.

If GitHub returns `401`, `403`, or `404`, the adapter logs a warning and returns no match for that request.

## Marking a Link Verified

When the code is found, storage marks the row verified:

```python
def mark_identity_verified(self, discord_user_id: str, github_user: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    gh_norm = github_user.strip().lower()
    with self._connect() as conn:
        conn.execute(
            """
            UPDATE identity_links
            SET verified = 1,
                verified_at = ?,
                verification_code = NULL,
                expires_at = NULL,
                unlinked_at = NULL
            WHERE discord_user_id = ? AND github_user_normalized = ?
            """,
            (now, discord_user_id, gh_norm),
        )
```

After this update:

- `verified = 1`
- `verified_at` is set to the current UTC time
- `verification_code` is cleared
- `expires_at` is cleared
- `unlinked_at` is cleared, which makes relinks active again

Verified mappings are loaded by other parts of the engine through:

```python
def list_verified_identity_mappings(self) -> list[IdentityMapping]:
    """Return verified identity mappings for engine usage."""
    with self._connect() as conn:
        rows = conn.execute(
            """
            SELECT discord_user_id, github_user
            FROM identity_links
            WHERE verified = 1
            ORDER BY discord_user_id ASC
            """
        ).fetchall()
    return [
        IdentityMapping(github_user=row["github_user"], discord_user_id=row["discord_user_id"])
        for row in rows
    ]
```

## Status and Stale Verification

The user can check status through:

- `/profile` (optional Discord member — omit for yourself)
- `ghdcbot identity status --discord-user-id <id>` (CLI)

`get_identity_status()` returns one of:

- `verified`
- `verified_stale`
- `pending`
- `not_linked`

```python
def get_identity_status(self, discord_user_id: str, max_age_days: int | None = None) -> dict:
    with self._connect() as conn:
        row = conn.execute(
            """
            SELECT discord_user_id, github_user, verified, verified_at
            FROM identity_links
            WHERE discord_user_id = ? AND (unlinked_at IS NULL)
            ORDER BY verified DESC, created_at DESC
            LIMIT 1
            """,
            (discord_user_id,),
        ).fetchone()
    if not row:
        return {"github_user": None, "status": "not_linked", "verified_at": None, "is_stale": False}
    if int(row["verified"] or 0) == 1:
        verified_at_raw = row["verified_at"]
        is_stale = False
        status = "verified"
        if verified_at_raw and max_age_days is not None and max_age_days > 0:
            verified_at = _parse_utc(verified_at_raw)
            age_days = (datetime.now(timezone.utc) - verified_at).days
            if age_days >= max_age_days:
                is_stale = True
                status = "verified_stale"
        return {
            "github_user": row["github_user"],
            "status": status,
            "verified_at": verified_at_raw,
            "is_stale": is_stale,
        }
    return {
        "github_user": row["github_user"],
        "status": "pending",
        "verified_at": None,
        "is_stale": False,
    }
```

Stale verification is soft. It warns the user and allows a refresh claim for the same Discord/GitHub pair, but it does not automatically unlink the user.

Configure it with:

```yaml
identity:
  verified_max_age_days: 30
```

## Unlink and Relink

`/unlink` uses the same service and calls `service.unlink(discord_user_id, cooldown)`.

Storage does not delete rows. It sets `verified = 0` and records `unlinked_at`.

```python
UPDATE identity_links
SET verified = 0, unlinked_at = ?
WHERE discord_user_id = ? AND github_user = ?
```

Unlinking has a cooldown after verification. The default is 24 hours:

```yaml
identity:
  unlink_cooldown_hours: 24
```

After unlinking, `get_identity_links_for_discord_user()` hides the unlinked row from active status views because it filters with `unlinked_at IS NULL`.

## CLI Commands

The CLI mirrors the Discord flow and is useful for testing or admin workflows.

Create a claim:

```bash
ghdcbot --config config/config.yaml link --discord-user-id 123456789 octocat
```

The CLI prints:

```text
Verification steps:
1) Put this code in your GitHub bio OR in a public GitHub gist: <CODE>
2) Re-run verification:
   ghdcbot --config config/config.yaml verify-link --discord-user-id 123456789 octocat
Expires at (UTC): <timestamp>
```

Verify the claim:

```bash
ghdcbot --config config/config.yaml verify-link --discord-user-id 123456789 octocat
```

Check status:

```bash
ghdcbot --config config/config.yaml identity status --discord-user-id 123456789
```

List verified contributors:

```bash
ghdcbot --config config/config.yaml identity list
```

## Audit Events

Identity verification writes append-only audit events to:

```text
<data_dir>/audit_events.jsonl
```

Events used by this system include:

- `identity_claim_created`
- `identity_verification_expired`
- `identity_verification_not_found`
- `identity_verified`
- `identity_unlinked`

An `identity_verified` event stores the GitHub user and the location where the code was found:

```json
{
  "actor_type": "discord_user",
  "actor_id": "123456789",
  "event_type": "identity_verified",
  "context": {
    "github_user": "octocat",
    "location": "bio"
  }
}
```

## Tested Behavior

`tests/test_identity_linking.py` verifies the important behavior:

- Verification codes are generated and stored.
- Already verified GitHub users cannot be claimed by another Discord user.
- Duplicate pending claims for the same GitHub user are rejected.
- Verified rows clear `verification_code` and `expires_at`.
- The Verify button marks a claim verified through `IdentityLinkService.verify_claim()`.
- The Verify button reports expired claims.
- The Verify button reports missing codes without disabling retry.
- The Cancel button disables the view.
- Unverified mappings are ignored by role planning.
- Unlinking removes active verified mappings without deleting history.
- Relinking works after unlinking.
- Status reports `not_linked`, `pending`, `verified`, and `verified_stale`.
- Stale verified users can refresh their claim.
- Non-stale verified users cannot create a duplicate refresh claim.

Run the focused tests with:

```bash
pytest tests/test_identity_linking.py
```

## Practical User Workflow

In Discord:

```text
/link github_username:octocat
```

Gitcord replies privately with a code.

The user adds the code to one of these public GitHub locations:

- GitHub profile bio
- Public gist description
- Public gist file content

Then the user runs:

```text
Click Verify
```

If the code is visible to the GitHub API and has not expired, Gitcord replies privately that the account is verified. From that point, the user appears in `list_verified_identity_mappings()` and can be used by contribution summaries, role planning, issue assignment, and other identity-aware features.

The manual fallback remains:

```text
/verify-link github_username:octocat
```

---

# Social Profiles: `/connect-social` and `/disconnect-social`

Contributors can optionally link X and LinkedIn by entering their username / profile URL manually. No external app registration or OAuth is required.

### Commands

- `/connect-social platform:` — choose **X** or **LinkedIn**, then enter your username or profile URL
- `/disconnect-social platform:` — remove that platform from Gitcord storage
- `/profile` — shows linked social profiles (read-only)

Input is normalized and validated (e.g. `@name`, `name`, or `https://x.com/name` for X; a LinkedIn profile URL for LinkedIn).

### Code map

| Path | Role |
|------|------|
| `src/ghdcbot/adapters/discord/social_commands.py` | `/connect-social`, `/disconnect-social` |
| `src/ghdcbot/engine/social_profiles.py` | Profile normalization, storage service |
| `src/ghdcbot/adapters/storage/sqlite.py` | `social_profiles` table |

---

## Manual Discord smoke (social profiles)

- [ ] `/connect-social` with platform X saves a valid username/URL
- [ ] `/connect-social` with invalid input shows a validation error
- [ ] `/disconnect-social` removes a linked platform
- [ ] `/profile` lists connected social profiles
- [ ] `/link` and `/verify-link` still work (no regression)

## References

- Profiles engine: `src/ghdcbot/engine/social_profiles.py`
- Commands: `src/ghdcbot/adapters/discord/social_commands.py`
- Env template: `.env.example`

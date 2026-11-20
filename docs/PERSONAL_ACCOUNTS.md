# Personal GitHub accounts and organizations

Gitcord monitors repositories under one GitHub **owner**. The owner can be a personal
account or an organization; neither requires the other, and no organization membership is
needed anywhere.

## Configuration

```yaml
github:
  owner: "MystifiedSky"     # login of the account that owns the repositories
  owner_type: "user"        # user | organization | auto (default: auto)
  token: "${GITHUB_TOKEN}"
  repos:
    mode: "allow"
    names: ["GitCraft"]
```

A complete example (including 1 / 5 / 15 merged-PR contributor roles) is in
[`config/examples/personal-account.yaml`](../config/examples/personal-account.yaml).

| `owner_type` | Behaviour |
|--------------|-----------|
| `user` | Personal account. No detection call. |
| `organization` | Organization. No detection call; uses `/orgs/{owner}/repos` exactly as before. |
| `auto` (default) | One `GET /users/{owner}` call (public, works for both) decides which of the above applies. |

`github.owner` is the preferred key. The legacy `github.org` key is still accepted and means
the same thing; setting both is allowed only when they name the same account. Either way
`config.github.org` / `config.github.owner` hold the same login, so nothing else in a
configuration has to change.

### Automatic detection

With `auto`, GitHub's `type` field (`User`, `Organization`, `Bot`) is used; bots are treated
as personal accounts. Failures are reported distinctly and never as a generic "not found":

| Situation | Message |
|-----------|---------|
| HTTP 404 | The account was not found: check the spelling. |
| HTTP 401 | Credentials were rejected; the account may exist. |
| HTTP 403 / 429 | Refused or rate limited; the account may exist. |
| No response | GitHub could not be reached; the account may exist. |

When the owner cannot be resolved, Gitcord logs the reason at `ERROR`, ingests nothing, and
marks repository discovery incomplete so the sync cursor does **not** advance. It never falls
back to a different owner's repositories.

## How repositories are discovered

**Organizations** use `GET /orgs/{owner}/repos`, unchanged. `github.user_fallback` keeps its
meaning (`/user/repos` when the token is not an org member), but the fallback list is now
restricted to repositories whose owner is the configured owner.

**Personal accounts:**

* With `repos.mode: allow`, each name is read directly with `GET /repos/{owner}/{name}`. The
  repository does not have to be publicly discoverable; any repository the token can read works.
  A 404 (missing, or private without access) skips that name with a warning; 401/403, rate limits
  and network failures mark discovery incomplete. Renamed repositories are followed through
  GitHub's redirect (update `repos.names` when you see the rename warning).
* Without an allowlist (or with `mode: deny`), repositories are listed:
  * token belongs to the owner: `GET /user/repos?affiliation=owner&visibility=all` (public and
    every private repository the token may read);
  * token belongs to someone else: `GET /users/{owner}/repos` (public) plus private
    repositories shared with that user (`/user/repos?affiliation=collaborator`);
  * GitHub App installation token: public repositories plus `/installation/repositories`.
* Results are paginated and always filtered to repositories whose `owner.login` matches the
  configured owner (case-insensitive). Allow/deny names are matched case-insensitively.

## Pull requests, issues and searches

Contribution ingestion already used repository endpoints (`/repos/{owner}/{repo}/...`), so PR
authors, merges, reviews, issues and assignees work the same for both owner types. Search-based
commands (`/open-prs`, `/pr`, PR lists) are scoped with `org:` for organizations and `user:`
for personal accounts; a personal account with an allowlist uses one `repo:owner/name` query per
allowlisted repository. Search hits belonging to another owner are discarded.

## Contributor rewards

Rewards come from the existing configurable systems: `merge_role_rules` (merged PR count),
`role_mappings` (scores) and `repo_contributor_roles`. Nothing is hard-coded to 1 / 5 / 15; that is
just the example. Activity is attributed to the verified Discord member via `/link` and
`/verify-link`, which only use public `GET /users/{login}` data and need no organization
membership. Re-ingesting history does not double count events, and merge-based roles are
promotion-only.

## Fine-grained token requirements

Create the token under **Settings -> Developer settings -> Fine-grained tokens**.

* **Resource owner:** your personal account (for a personal-account deployment).
* **Repository access:** *Only select repositories* (for example `GitCraft`) is enough for an
  allowlist; add every repository you list in `repos.names`. A private repository is visible only
  if it is selected here.
* **Permissions (read-only / dry-run):** Metadata (read), Contents (read), Issues (read), Pull
  requests (read). Add Checks (read) and Commit statuses (read) for CI-based scoring.
* **Permissions (active mode):** Issues and Pull requests read & write if you use assignments or
  review requests.

Keep the token in the environment (`GITHUB_TOKEN` in `.env`); it is never written to logs.
Public repositories are discoverable even with a token that cannot see your private ones; private
repositories are included only when the token is authorised for them.

## Migrating from `github.org`

1. No change is required: `org: "MystifiedSky"` now resolves through `owner_type: auto`.
2. Optionally rename the key to `owner:` and set `owner_type` explicitly to skip the one detection
   call per process.
3. Keep `runtime.mode: dry-run` while checking `<data_dir>/reports/audit.md`.
4. Existing SQLite databases, stored identity links and environment variable names are untouched.

## Limitations on personal accounts

* There are no organization teams or membership: `has_write_access` (used by `/assign-issue`
  style checks) relies only on repository collaborator permission.
* `/pr-channel` and repository autocomplete list only repositories owned by the configured owner.
* One owner per Gitcord instance (unchanged).
* A token can only see what GitHub grants it; a 404 for a private repository is indistinguishable
  from a missing one.

## Troubleshooting

| Log message | Meaning / fix |
|-------------|---------------|
| `GitHub account 'X' was not found` | Typo in `github.owner`, or the account is suspended. |
| `rejected the configured credentials` | Token expired or revoked; the account itself is fine. |
| `GitHub organization repositories not found ... personal account` | `owner_type: organization` on a user. Use `user` or `auto`. |
| `Allowlisted repository not found or not accessible` | Name misspelt, or the token was not granted that private repository. |
| `Allowlisted repository was renamed` | Update the name in `repos.names`. |
| `Repository discovery failed for personal account` | Token lacks access or is rate limited; the sync cursor is held back and retried next run. |

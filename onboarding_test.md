# Organization Onboarding Audit (Week 4 Day 5)

**Scenario:** A new open-source organization (not AOSSIE) wants Gitcord for contributor role automation and GitHub↔Discord identity linking.

**Sources reviewed:** `INSTALLATION.md` (workspace), `README.md`, `docs/DOCKER.md`, `environment_variables.md`, `config/example.yaml`, `config/docker-example.yaml`, fresh-install simulation on `HEAD`.

---

## Onboarding flow (intended)

```text
GitHub PAT ──┐
             ├──► .env + config/config.yaml ──► validate ──► run-once (dry-run) ──► bot ──► active mode
Discord bot ─┘
```

On **`HEAD` today**, the `validate` step and much of the updated INSTALLATION path **do not exist** for clones.

---

## Discord onboarding

| Step | Documented? | Friction | Common mistake |
|------|-------------|----------|----------------|
| Create application | ✅ Step 2 | Low | — |
| Create bot + copy token | ✅ | Low | Forgetting to copy before leaving page |
| **Server Members Intent** | ✅ | **Medium** | Easy to skip; causes empty member lists later |
| OAuth URL scopes (`bot`, `applications.commands`) | ✅ Step 3 | Low | Using Administrator scope unnecessarily |
| Invite to correct server | ✅ | Medium | Inviting to personal server instead of org server |
| Bot role position | ✅ Step 3.5 | **High** | Bot role below `Contributor` → silent role-assign failures |
| Copy guild ID | ✅ Step 3.6 | **Medium** | Developer Mode not enabled; wrong ID pasted |
| Match role names in YAML | ⚠️ Partial | **High** | Template lists `Contributor`, `Maintainer`, `Mentor`, plus `castro` in `repo_contributor_roles` — none guaranteed to exist |

### Most confusing Discord step

**Bot role hierarchy (Step 3.5).** Documentation explains it, but Discord’s UI is non-obvious and the failure mode is silent until `run-once` or active mode.

### Most common Discord mistake

**Wrong `discord.guild_id`** — often the admin’s test server ID, or the placeholder `000000000000000000` left from the template. Symptom: slash commands missing or bot “works” on wrong server.

### Could Discord setup be automated?

| Item | Automatable? |
|------|--------------|
| Bot creation | ❌ Requires Discord Developer Portal login |
| Invite URL | ⚠️ Partial — could generate URL from client ID + permission bitmask in docs/tool |
| Guild ID | ❌ User must copy from client |
| Role existence check | ✅ **`ghdcbot validate`** (workspace only) |
| Role hierarchy check | ⚠️ Partial — API can list roles but cannot verify drag-order; could warn if bot lacks Manage Roles |

---

## GitHub onboarding

| Step | Documented? | Friction | Common mistake |
|------|-------------|----------|----------------|
| Fine-grained PAT | ✅ Step 1 | **High** | Many users default to classic tokens |
| **Org as resource owner** | ✅ | **High** | PAT scoped to personal account → empty org repo list |
| Repository access selection | ✅ | Medium | Too narrow → missing repos; too broad → security review pushback |
| Org approval for PAT | ⚠️ Implied | **High** | Token pending org owner approval — looks “broken” for hours |
| Permissions (Contents/Issues/PRs) | ✅ | Medium | Read-only OK for dry-run; write needed later — not obvious when to upgrade |
| `github.org` in config | ✅ | Low | Typo or wrong casing |

### Most confusing GitHub step

**Fine-grained PAT with organization resource owner + repository scope.** Classic PAT docs elsewhere on the internet conflict with Gitcord’s guide.

### Most common GitHub mistake

**PAT cannot see org repos** — personal resource owner, or org has not approved the token. `run-once` on `HEAD` logs warnings and produces an empty audit; user thinks org has no activity.

### Could GitHub setup be automated?

| Item | Automatable? |
|------|--------------|
| PAT creation | ❌ Requires GitHub UI / org policy |
| Org access check | ✅ `ghdcbot validate` → `GET /orgs/{org}`, `GET /orgs/{org}/repos` |
| Permission level hint | ⚠️ Could inspect token scopes via API and warn if write needed for active mode |

---

## Cross-cutting onboarding gaps

### 1. Template config ≠ real Discord server

`config/example.yaml` and `config/docker-example.yaml` ship with:

- `discord.guild_id: "000000000000000000"`
- Roles: `Contributor`, `Maintainer`, `Mentor`
- `repo_contributor_roles: { castro: "castro" }` — **leftover example** that fails `validate` role checks

New orgs must edit **both** `.env` and **multiple** YAML fields before any command succeeds meaningfully.

### 2. No single “you are ready” gate on `HEAD`

Without `validate`, the first feedback is either:

- Docker stack trace (bad Discord token), or
- JSON warning stream + empty audit (bad GitHub token)

Neither says clearly: “Stop — fix `.env` first.”

### 3. Active mode promotion is easy to get wrong

Requires **four** flags (documented in workspace INSTALLATION):

- `runtime.mode: active`
- `runtime.enable_discord_role_updates: true`
- `discord.permissions.write: true`
- Bot role hierarchy

Users who only flip `mode: active` see no role changes — feels broken.

### 4. AOSSIE-centric artifacts

Clone URL defaults to `AOSSIE-Org/Gitcord-GithubDiscordBot`. `config/examples/aussie.yaml` is helpful reference but can imply “copy this” to new orgs. Not blocking if `config/config.yaml` convention is clear.

---

## Simulation verdict

| Question | Answer |
|----------|--------|
| Can a new org onboard with **only** `HEAD` docs? | **Not reliably** — doc split (`my-org-config.yaml` vs `config.yaml`), no validate, false-success `run-once` |
| Can a new org onboard with **workspace** docs + code? | **Yes**, if they have Discord admin + GitHub org PAT — ~35–70 min first time |
| Single step most likely to stall onboarding | **GitHub fine-grained PAT org approval + repo scope** |
| Single step most likely to cause silent failure | **Discord bot role position** or **wrong guild_id** |

---

## Recommended onboarding order (after Week 4 commit)

1. Create GitHub PAT (org owner, repo access)
2. Create Discord bot (Members Intent), invite, fix role order, copy guild ID
3. Clone repo → `cp` templates → edit `.env` + `config/config.yaml`
4. **`ghdcbot validate`** — fix all ✗ before continuing
5. **`run-once`** (dry-run) → read `<data_dir>/reports/audit.md`
6. Start bot → test `/link`
7. Enable active mode flags only after audit review

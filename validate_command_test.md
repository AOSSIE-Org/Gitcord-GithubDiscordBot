# Validate Command Test (Week 4 Day 5)

**Command:** `ghdcbot --config config/config.yaml validate`

**Availability:** Implemented in **workspace** (`src/ghdcbot/config/setup_validate.py`, `cli.py`). **Not present** on `HEAD` / fresh `git clone` — confirmed on isolated clone:

```text
ghdcbot: error: argument command: invalid choice: 'validate'
```

Tests below use the **workspace** build (`.venv/bin/ghdcbot`). Automated tests: `tests/test_validate.py` (5 cases) + `tests/test_config_validation.py` (9 cases) — **14 passed**.

---

## Test matrix

### 1. Correct config (AOSSIE production config)

**Setup:** Real `.env` tokens, `config/config.yaml` (AOSSIE-Org, guild `1022871757289422898`, `enable_discord_role_updates: false`).

**Command:**

```bash
ghdcbot --config config/config.yaml validate
```

**Result:** ✅ **PASS** (exit 0)

```text
✓ Config file loaded
✓ YAML valid and schema OK
✓ GITHUB_TOKEN present
✓ DISCORD_TOKEN present
✓ GitHub authentication successful
  Authenticated as <user>.
✓ Organization accessible
  AOSSIE-Org
✓ Repositories visible
  5+ repo(s) visible (e.g. …)
✓ Discord authentication successful
  Bot user: Gitcord
✓ Guild found
  Guild: AOSSIE
⚠ Role mapping check skipped
  runtime.enable_discord_role_updates is false.

Validation passed.
```

**Notes:** Role checks correctly skipped when role updates disabled. Good for observer/dry-run orgs.

---

### 2. Missing GitHub token

**Expected:** FAIL with helpful message at config load.

**Test A — `${GITHUB_TOKEN}` unset, no `.env`:** Run in an isolated environment so `load_dotenv()` cannot pick up local machine values. For example, use `env -i` (or explicitly `unset GITHUB_TOKEN DISCORD_TOKEN`) and run from a temp directory without a `.env` file before invoking validate.

**Test B — inline invalid token (isolated `/tmp` config, no `${}` placeholders):**

```yaml
github:
  token: "not-a-real-github-token"
discord:
  token: "not-a-real-discord-token"
```

**Result:** ✅ **FAIL** (exit 1)

```text
✓ GITHUB_TOKEN present        # misleading label — token string exists in YAML
✓ DISCORD_TOKEN present
✗ GitHub authentication failed
  Check GITHUB_TOKEN permissions and expiration.
✗ Discord authentication failed
  Check DISCORD_TOKEN in your .env file.
```

**Test C — empty env var (workspace Day 3 loader):**

```bash
GITHUB_TOKEN= ghdcbot --config config/example.yaml validate
```

**Result:** ✅ **FAIL** at load

```text
GITHUB_TOKEN is configured but empty.
Please set a valid GitHub Personal Access Token.
```

**Gap:** Lines still say `✓ GITHUB_TOKEN present` before API checks when token is present but invalid. Label means “field expanded”, not “token works”.

---

### 3. Missing Discord token

**Empty `DISCORD_TOKEN` via env expansion:**

**Result:** ✅ **FAIL** at load (workspace loader)

```text
DISCORD_TOKEN is configured but empty.
```

**Invalid Discord token (inline):** ✅ **FAIL**

```text
✗ Discord authentication failed
  Check DISCORD_TOKEN in your .env file.
```

---

### 4. Wrong guild ID

**Setup:** Valid tokens, `discord.guild_id: "999999999999999999"`.

**Result:** ✅ **FAIL** (exit 1)

```text
✓ GitHub authentication successful
✓ Discord authentication successful
✗ Guild not found
  Bot is not in this server or discord.guild_id is wrong.
✗ Role "Contributor" not found
  …
```

**Helpful:** Guild failure message is clear.

**Noise:** Adapter logs leak to stdout before formatted output:

```text
Unable to list roles
```

Should be stderr-only or suppressed during validate.

---

### 5. Missing roles (template config against real guild)

**Setup:** `config/example.yaml` against AOSSIE guild with `enable_discord_role_updates: true`.

**Result:** ✅ **FAIL** (exit 1) — expected roles

```text
✓ Role "Contributor" found
✓ Role "Mentor" found
✗ Role "Maintainer" not found
✗ Role "castro" not found
```

**Finding:** Template `repo_contributor_roles: { castro: "castro" }` causes validate failure for every org unless removed.

---

## Comparison: validate vs run-once vs bot

| Scenario | `validate` | `run-once` (`HEAD`) | `bot` (`HEAD`) |
|----------|------------|---------------------|----------------|
| Bad GitHub token | ✗ clear | ⚠️ warnings, exit 0 | N/A |
| Bad Discord token | ✗ clear | ⚠️ warnings, exit 0 | Traceback loop |
| Wrong guild | ✗ clear | ⚠️ empty roles | May login but wrong server |
| Missing role names | ✗ clear | ⚠️ silent empty plan | N/A until sync |
| Good config | ✓ exit 0 | ✓ audit generated | ✓ bot online |

**Conclusion:** `validate` is the right preflight gate. It must be **committed** and **documented before `run-once`/`up -d`** in INSTALLATION.

---

## Automated test coverage

| Test file | Cases |
|-----------|-------|
| `tests/test_validate.py` | Valid config, missing GitHub token (load_config), invalid Discord, missing guild, missing role |
| `tests/test_config_validation.py` | Missing/empty tokens, YAML errors, active mode, placeholder guild |

---

## Remaining validate issues (low/medium)

1. **Misleading “TOKEN present” lines** when token is invalid — consider `✓ GITHUB_TOKEN configured` vs `✓ GitHub authentication successful`.
2. **Adapter log noise** on stdout during validate (`Unable to list roles`).
3. **Not in released code** until commit/push — blocks all Day 5 value for external orgs.
4. **No `docker compose` example on `HEAD`** pointing to validate before `up -d`.

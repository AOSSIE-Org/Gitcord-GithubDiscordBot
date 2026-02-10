# When Are Snapshots Created?

This document explains exactly when and under what conditions GitHub snapshots are created.

## Snapshot Creation Triggers

Snapshots are created **automatically** when you run Gitcord's main processing cycle. Here are the specific situations:

---

## 1. When You Run `/sync` Command (Discord Bot)

**Situation:** You run the `/sync` slash command in Discord (mentor-only command)

**What happens:**
1. Bot triggers `orchestrator.run_once()`
2. Gitcord processes GitHub events, computes scores, plans assignments
3. **After all processing completes**, snapshots are written to GitHub
4. Snapshots are created even if no new events were found

**Example:**
```
User: /sync
Bot: ✅ Sync completed successfully
     → Snapshot created: snapshots/2026-02-10T17-23-39-e5dacbc2/
```

---

## 2. When You Run `run-once` Command (CLI)

**Situation:** You run Gitcord from command line:
```bash
python -m ghdcbot --config config/shubh-olrd.yaml run-once
```

**What happens:**
1. Gitcord ingests GitHub events
2. Computes scores
3. Plans Discord roles and GitHub assignments
4. Applies plans (if in active mode)
5. **After all processing completes**, snapshots are written

**Example:**
```bash
$ python -m ghdcbot --config config/shubh-olrd.yaml run-once
...
INFO: GitHub snapshots written
```

---

## 3. After Every `run-once` Cycle Completes

**Important:** Snapshots are created **after** the entire `run-once` cycle completes successfully, regardless of:
- Whether new events were found
- Whether scores changed
- Whether assignments were made
- Whether Discord roles were updated

**Timeline:**
```
run-once starts
  ↓
Ingest GitHub events
  ↓
Compute scores
  ↓
Plan assignments/roles
  ↓
Send notifications (if enabled)
  ↓
Apply GitHub plans (if active mode)
  ↓
Apply Discord roles (if active mode)
  ↓
Write reports (if dry-run/observer mode)
  ↓
✅ Write snapshots ← ALWAYS happens here (if enabled)
  ↓
run-once completes
```

---

## 4. Snapshot Creation Requirements

For snapshots to be created, **ALL** of these must be true:

### ✅ Configuration Requirements

1. **Snapshots must be enabled:**
   ```yaml
   snapshots:
     enabled: true  # Must be true
   ```

2. **Repository path must be configured:**
   ```yaml
   snapshots:
     repo_path: "owner/repo"  # Must be valid format
   ```

3. **GitHub token must have write access** to the repository

### ✅ Runtime Requirements

1. **`run-once` must complete successfully** (snapshots are written at the end)
2. **GitHub API must be accessible** (network, authentication)
3. **Repository must exist** and be accessible

---

## 5. When Snapshots Are NOT Created

Snapshots will **NOT** be created if:

### ❌ Configuration Disabled
```yaml
snapshots:
  enabled: false  # Snapshots disabled
```

### ❌ Missing Configuration
```yaml
# No snapshots section in config
```

### ❌ GitHub Write Fails (Non-Blocking)
- If GitHub API is down
- If token doesn't have write permissions
- If repository doesn't exist
- **Note:** `run-once` still completes successfully, but no snapshot is created

### ❌ `run-once` Fails Early
- If there's an error before snapshot writing step
- Snapshot writing happens at the very end, so early failures prevent snapshots

---

## 6. Snapshot Frequency

**Current Behavior:**
- **One snapshot per `run-once` execution**
- **No automatic scheduling** (Gitcord doesn't run automatically)
- **Manual trigger only** (via `/sync` or CLI `run-once`)

**Example Timeline:**
```
10:00 AM - Run /sync → Snapshot 1 created
10:15 AM - Run /sync → Snapshot 2 created
10:30 AM - Run /sync → Snapshot 3 created
```

Each snapshot is timestamped and unique - previous snapshots are never overwritten.

---

## 7. What Gets Snapshot'd

Every snapshot captures the **current state** at the time of `run-once`:

- ✅ **Identities**: All verified Discord ↔ GitHub links
- ✅ **Scores**: Current contributor scores for the period
- ✅ **Contributors**: Contribution summaries (if available)
- ✅ **Roles**: Current Discord member roles
- ✅ **Issue Requests**: Pending issue assignment requests
- ✅ **Notifications**: Recent sent notifications (last 1000)
- ✅ **Metadata**: Run ID, timestamps, period info

**Note:** Even if data is empty (e.g., no issue requests), the snapshot file is still created with an empty `data` array.

---

## 8. Example Scenarios

### Scenario 1: Daily Sync
```bash
# Morning sync
python -m ghdcbot --config config.yaml run-once
# → Snapshot created: snapshots/2026-02-10T09-00-00-abc12345/

# Evening sync
python -m ghdcbot --config config.yaml run-once
# → Snapshot created: snapshots/2026-02-10T18-00-00-def67890/
```

### Scenario 2: After Issue Assignment
```
1. Mentor assigns issue via /assign-issue
2. Mentor runs /sync
3. → Snapshot created with updated state
```

### Scenario 3: After Score Update
```
1. New PR merged → score increases
2. Run /sync
3. → Snapshot created with new scores
```

### Scenario 4: Empty State
```
1. No new events, no changes
2. Run /sync
3. → Snapshot still created (captures current state)
```

---

## 9. Snapshot Timing

**When exactly are snapshots written?**

Snapshots are written **at the very end** of `run-once`, after:
- ✅ All GitHub events ingested
- ✅ All scores computed
- ✅ All plans created
- ✅ All notifications sent
- ✅ All GitHub assignments applied (if active mode)
- ✅ All Discord roles updated (if active mode)
- ✅ All reports written (if dry-run/observer mode)

**This ensures:**
- Snapshots capture the **final state** after all processing
- Snapshots are **consistent** with what was actually applied
- Snapshots are **non-blocking** (failures don't affect `run-once`)

---

## 10. Checking If Snapshots Are Being Created

### Check Logs
```bash
# Look for snapshot messages
grep -i "snapshot" bot.log

# Should see:
# "GitHub snapshots written"
# "File written to GitHub"
```

### Check GitHub Repository
```bash
# List snapshots
curl -H "Authorization: Bearer TOKEN" \
  https://api.github.com/repos/owner/repo/contents/snapshots | jq '.[].name'
```

### Check Latest Snapshot
```bash
# Get latest snapshot directory
LATEST=$(curl -s -H "Authorization: Bearer TOKEN" \
  https://api.github.com/repos/owner/repo/contents/snapshots | jq -r '.[-1].name')

echo "Latest snapshot: $LATEST"
```

---

## Summary

**Snapshots are created:**
- ✅ After every successful `run-once` execution
- ✅ When `/sync` command is run
- ✅ When `run-once` CLI command is run
- ✅ If snapshots are enabled in config
- ✅ If GitHub write succeeds

**Snapshots are NOT created:**
- ❌ If `snapshots.enabled: false`
- ❌ If GitHub write fails (non-blocking)
- ❌ If `run-once` fails before snapshot step
- ❌ Automatically on a schedule (manual trigger only)

**Key Point:** Snapshots are created **once per `run-once` execution**, capturing the complete state after all processing is done.

# Testing GitHub Snapshots

This guide shows you how to test and verify that GitHub snapshots are working correctly.

## Prerequisites

1. **GitHub Repository**: Create a repository to store snapshots (e.g., `my-org/gitcord-data`)
2. **GitHub Token**: Ensure your GitHub token has write access to the repository
3. **Config File**: Your Gitcord config YAML file

## Step 1: Enable Snapshots in Config

Edit your config YAML file (e.g., `config/shubh-olrd.yaml`) and add the `snapshots` section:

```yaml
snapshots:
  enabled: true
  repo_path: "your-org/gitcord-data"  # Replace with your actual repo
  branch: "main"  # Optional: defaults to repo's default branch
```

**Example:**
```yaml
snapshots:
  enabled: true
  repo_path: "shubh-olrd/gitcord-data"
  branch: "main"
```

## Step 2: Run Gitcord

### Option A: Using `/sync` Command (Discord Bot)

1. Start the bot:
   ```bash
   python -m ghdcbot --config config/shubh-olrd.yaml bot
   ```

2. In Discord, run the `/sync` command (mentor-only)

3. Check bot logs for snapshot messages:
   ```bash
   tail -f bot.log | grep -i snapshot
   ```

### Option B: Using `run-once` Command

```bash
python -m ghdcbot --config config/shubh-olrd.yaml run-once
```

## Step 3: Check Logs for Snapshot Activity

Look for log messages indicating snapshot writing:

### Success Messages
```
INFO: Snapshots: GitHub snapshots written
  extra={"org": "your-org", "repo": "your-org/gitcord-data", "snapshot_dir": "snapshots/2024-01-31T12-00-00-abc12345", "files": 7}
```

### Failure Messages (Non-Blocking)
```
WARNING: Snapshots: Failed to write GitHub snapshots (non-blocking)
  extra={"error": "...", "org": "your-org"}
```

### Disabled Messages
If snapshots are disabled, you won't see any snapshot-related logs.

## Step 4: Verify Snapshots on GitHub

### Using GitHub Web UI

1. Navigate to your snapshot repository: `https://github.com/your-org/gitcord-data`
2. Check for a new `snapshots/` directory
3. Inside, you should see timestamped directories like:
   ```
   snapshots/
     └── 2024-01-31T12-00-00-abc12345/
         ├── meta.json
         ├── identities.json
         ├── scores.json
         ├── contributors.json
         ├── roles.json
         ├── issue_requests.json
         └── notifications.json
   ```

### Using GitHub CLI

```bash
# List snapshot directories
gh repo view your-org/gitcord-data --json defaultBranchRef | jq -r '.defaultBranchRef.name'
gh api repos/your-org/gitcord-data/contents/snapshots

# View a specific snapshot file
gh api repos/your-org/gitcord-data/contents/snapshots/2024-01-31T12-00-00-abc12345/meta.json | jq -r '.content' | base64 -d
```

### Using Git

```bash
# Clone the repo
git clone https://github.com/your-org/gitcord-data.git
cd gitcord-data

# List snapshots
ls -la snapshots/

# View a snapshot file
cat snapshots/2024-01-31T12-00-00-abc12345/meta.json | jq
```

## Step 5: Validate Snapshot Files

### Check File Structure

Each snapshot file should have this structure:

```json
{
  "schema_version": "1.0.0",
  "generated_at": "2024-01-31T12:00:00+00:00",
  "org": "your-org",
  "run_id": "abc12345-def6-7890-ghij-klmnopqrstuv",
  "data": [...]
}
```

### Validate JSON

```bash
# Check if JSON is valid
cat snapshots/2024-01-31T12-00-00-abc12345/meta.json | jq . > /dev/null && echo "Valid JSON" || echo "Invalid JSON"

# Pretty print
cat snapshots/2024-01-31T12-00-00-abc12345/meta.json | jq
```

### Check Specific Files

**meta.json:**
```bash
cat snapshots/2024-01-31T12-00-00-abc12345/meta.json | jq '.schema_version, .org, .run_id'
```

**identities.json:**
```bash
cat snapshots/2024-01-31T12-00-00-abc12345/identities.json | jq '.data | length'
cat snapshots/2024-01-31T12-00-00-abc12345/identities.json | jq '.data[0]'
```

**scores.json:**
```bash
cat snapshots/2024-01-31T12-00-00-abc12345/scores.json | jq '.data | length'
cat snapshots/2024-01-31T12-00-00-abc12345/scores.json | jq '.data[0]'
```

## Step 6: Test Multiple Runs

Run `run-once` or `/sync` multiple times to verify:

1. **Each run creates a new snapshot directory** (timestamped)
2. **Previous snapshots are not overwritten**
3. **Snapshot data reflects current state**

```bash
# Run multiple times
python -m ghdcbot --config config/shubh-olrd.yaml run-once
sleep 5
python -m ghdcbot --config config/shubh-olrd.yaml run-once
sleep 5
python -m ghdcbot --config config/shubh-olrd.yaml run-once

# Check that multiple snapshots exist
ls -la snapshots/ | wc -l
```

## Step 7: Test Error Handling

### Test with Invalid Repo Path

Temporarily set an invalid repo path to verify error handling:

```yaml
snapshots:
  enabled: true
  repo_path: "invalid-repo-path"  # Invalid format
```

Run `run-once` - it should:
- ✅ Complete successfully (non-blocking)
- ✅ Log a warning about snapshot failure
- ✅ Not crash or affect other functionality

### Test with Non-Existent Repo

```yaml
snapshots:
  enabled: true
  repo_path: "your-org/non-existent-repo"
```

Run `run-once` - it should:
- ✅ Complete successfully
- ✅ Log a warning about GitHub API error
- ✅ Not affect other functionality

## Step 8: Verify Audit Logging

Check that snapshot writes are logged to audit log:

```bash
# If you have audit log file
grep -i "snapshot_written" audit.log

# Or check storage for audit events
python3 << EOF
from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.loader import load_config

config = load_config("config/shubh-olrd.yaml")
storage = SqliteStorage(config.runtime.data_dir)
# Check for snapshot_written events in audit log
EOF
```

## Troubleshooting

### Snapshots Not Being Written

1. **Check config**: Verify `snapshots.enabled: true`
2. **Check repo path**: Ensure format is `owner/repo`
3. **Check GitHub token**: Verify token has write access
4. **Check logs**: Look for error messages
5. **Check GitHub API**: Verify repository exists and is accessible

### Invalid JSON Files

1. **Check file encoding**: Should be UTF-8
2. **Check file size**: Should not be empty
3. **Validate manually**: Use `jq` or Python `json.load()`

### Missing Files

All 7 files should be present:
- `meta.json` ✅
- `identities.json` ✅
- `scores.json` ✅
- `contributors.json` ✅
- `roles.json` ✅
- `issue_requests.json` ✅
- `notifications.json` ✅

If files are missing, check logs for specific errors.

## Quick Test Script

Save this as `test_snapshots.sh`:

```bash
#!/bin/bash
set -e

CONFIG="config/shubh-olrd.yaml"
REPO="your-org/gitcord-data"

echo "1. Running run-once..."
python -m ghdcbot --config "$CONFIG" run-once

echo "2. Checking logs for snapshot activity..."
grep -i "snapshot" bot.log | tail -5

echo "3. Verifying snapshot on GitHub..."
LATEST_SNAPSHOT=$(gh api repos/$REPO/contents/snapshots | jq -r '.[-1].name' 2>/dev/null || echo "none")
if [ "$LATEST_SNAPSHOT" != "none" ]; then
    echo "✅ Latest snapshot: $LATEST_SNAPSHOT"
    echo "4. Checking snapshot files..."
    gh api repos/$REPO/contents/snapshots/$LATEST_SNAPSHOT | jq -r '.[].name'
else
    echo "❌ No snapshots found"
fi
```

Make it executable and run:
```bash
chmod +x test_snapshots.sh
./test_snapshots.sh
```

## Expected Behavior

✅ **Success Case:**
- `run-once` completes successfully
- Log shows "GitHub snapshots written" with file count
- GitHub repo has new snapshot directory
- All 7 JSON files are present and valid
- Previous snapshots are not overwritten

✅ **Failure Case (Non-Blocking):**
- `run-once` completes successfully
- Log shows warning about snapshot failure
- Other functionality unaffected
- No snapshot directory created (or partial)

## Next Steps

Once snapshots are working:
1. **Monitor**: Check snapshots are written regularly
2. **Verify data**: Ensure snapshot data matches SQLite state
3. **Org Explorer**: Update Org Explorer to consume snapshots
4. **Cleanup**: Consider snapshot retention policy (future)

# Troubleshooting: Snapshots Not Created After /sync

## Problem
You run `/sync` in Discord but don't see snapshots created in GitHub.

## Quick Checks

### 1. Verify Snapshots Are Enabled in Config

Check your `config/shubh-olrd.yaml`:

```yaml
snapshots:
  enabled: true  # Must be true
  repo_path: "shubham-orld/gitcord-data"
  branch: "main"
```

**Fix:** If missing or `enabled: false`, add/update the config and restart the bot.

---

### 2. Check Bot Logs

After running `/sync`, check the bot logs:

```bash
# If bot is running with nohup
tail -f bot.log | grep -i snapshot

# Or check recent logs
tail -100 bot.log | grep -E "(snapshot|sync|run-once)" -i
```

**What to look for:**
- ✅ `"GitHub snapshots written"` - Snapshots created successfully
- ✅ `"File written to GitHub"` - Files being written
- ❌ `"Failed to write GitHub snapshots"` - Snapshot creation failed
- ❌ `"Exception writing file to GitHub"` - Error writing files

---

### 3. Verify Bot Has Latest Code

The snapshot feature was recently added. Make sure your bot is running the latest code:

```bash
# Check if bot process is running old code
ps aux | grep "python.*ghdcbot.*bot"

# Restart bot to load latest code
pkill -f "python.*ghdcbot.*bot"
nohup .venv/bin/python -m ghdcbot --config config/shubh-olrd.yaml bot > bot.log 2>&1 &
```

---

### 4. Check GitHub Repository Access

Verify the bot's GitHub token can write to the repository:

```bash
# Test repository access
curl -H "Authorization: Bearer YOUR_TOKEN" \
  https://api.github.com/repos/shubham-orld/gitcord-data

# Should return repository info, not 404 or 403
```

**Common issues:**
- ❌ Token doesn't have write access
- ❌ Repository doesn't exist
- ❌ Token expired

---

### 5. Check /sync Command Response

After running `/sync`, the bot should respond:

**Success:**
```
✅ Sync complete! Notifications sent for new GitHub events.
```

**Failure:**
```
❌ Sync failed: [error message]
```

If you see an error, check the error message for clues.

---

### 6. Verify Snapshots Are Actually Created

Even if you don't see a message, snapshots might still be created. Check GitHub:

```bash
# List all snapshots
curl -H "Authorization: Bearer YOUR_TOKEN" \
  https://api.github.com/repos/shubham-orld/gitcord-data/contents/snapshots | jq '.[].name'

# Or check on GitHub web UI:
# https://github.com/shubham-orld/gitcord-data/tree/main/snapshots
```

**If snapshots exist but you didn't see them:**
- Snapshots are created silently (no Discord message)
- Check GitHub repository directly
- Check bot logs for confirmation

---

## Step-by-Step Debugging

### Step 1: Test with CLI First

Before testing `/sync`, verify snapshots work with CLI:

```bash
python -m ghdcbot --config config/shubh-olrd.yaml run-once
```

**Check output:**
- Look for `"GitHub snapshots written"` in output
- Check GitHub for new snapshot directory

**If CLI works but `/sync` doesn't:**
- Bot might not have latest code
- Bot might not be running
- Check bot logs for errors

---

### Step 2: Check Bot Is Running

```bash
# Check if bot process exists
ps aux | grep "python.*ghdcbot.*bot" | grep -v grep

# If not running, start it:
nohup .venv/bin/python -m ghdcbot --config config/shubh-olrd.yaml bot > bot.log 2>&1 &

# Wait a few seconds, then check logs
tail -20 bot.log
```

---

### Step 3: Test /sync Command

1. Run `/sync` in Discord
2. Wait for bot response
3. Check bot logs immediately:

```bash
tail -50 bot.log | grep -E "(sync|snapshot|run-once|orchestrator)" -i
```

**What to look for:**
- `"Syncing GitHub events..."` - Command received
- `"GitHub snapshots written"` - Snapshots created
- Any error messages

---

### Step 4: Verify Snapshot Creation

After running `/sync`, check GitHub:

```bash
# Get latest snapshot
LATEST=$(curl -s -H "Authorization: Bearer YOUR_TOKEN" \
  https://api.github.com/repos/shubham-orld/gitcord-data/contents/snapshots | \
  jq -r '.[-1].name')

echo "Latest snapshot: $LATEST"

# Check timestamp (should be recent)
echo "$LATEST" | grep "$(date +%Y-%m-%d)"
```

**If snapshot timestamp matches current time:**
- ✅ Snapshots are being created
- ✅ `/sync` is working
- You just need to check GitHub to see them

---

## Common Issues and Fixes

### Issue 1: Snapshots Disabled in Config

**Symptom:** No snapshots created, no errors in logs

**Fix:**
```yaml
snapshots:
  enabled: true  # Change from false to true
  repo_path: "shubham-orld/gitcord-data"
```

**Then restart bot.**

---

### Issue 2: Bot Running Old Code

**Symptom:** `/sync` works but no snapshots

**Fix:**
```bash
# Stop bot
pkill -f "python.*ghdcbot.*bot"

# Start bot with latest code
nohup .venv/bin/python -m ghdcbot --config config/shubh-olrd.yaml bot > bot.log 2>&1 &
```

---

### Issue 3: GitHub Write Fails Silently

**Symptom:** `/sync` completes but no snapshots, errors in logs

**Check logs:**
```bash
grep -i "failed\|error\|exception" bot.log | tail -20
```

**Common causes:**
- Token doesn't have write access
- Repository doesn't exist
- Network issues

**Fix:** Check GitHub token permissions and repository access.

---

### Issue 4: Snapshots Created But Not Visible

**Symptom:** Logs show "GitHub snapshots written" but you don't see them

**Check:**
1. Look in correct repository: `shubham-orld/gitcord-data`
2. Look in `snapshots/` directory
3. Check correct branch (usually `main`)

**Fix:** Navigate to GitHub repository and check `snapshots/` directory.

---

## Expected Behavior

### When /sync Works Correctly

1. **Discord:** Bot responds with "✅ Sync complete!"
2. **Logs:** Shows "GitHub snapshots written"
3. **GitHub:** New snapshot directory created in `snapshots/`
4. **Files:** 7 JSON files in snapshot directory

### Snapshot Directory Format

```
snapshots/
  └── 2026-02-10T17-23-39-e5dacbc2/
      ├── meta.json
      ├── identities.json
      ├── scores.json
      ├── contributors.json
      ├── roles.json
      ├── issue_requests.json
      └── notifications.json
```

---

## Quick Test Script

Run this to test snapshot creation:

```bash
#!/bin/bash
echo "1. Checking config..."
grep -A 3 "snapshots:" config/shubh-olrd.yaml

echo ""
echo "2. Testing CLI run-once..."
python -m ghdcbot --config config/shubh-olrd.yaml run-once 2>&1 | grep -i snapshot

echo ""
echo "3. Checking GitHub for snapshots..."
curl -s -H "Authorization: Bearer YOUR_TOKEN" \
  https://api.github.com/repos/shubham-orld/gitcord-data/contents/snapshots | \
  jq -r '.[-1].name'

echo ""
echo "4. Checking bot logs..."
tail -20 bot.log | grep -i snapshot
```

---

## Still Not Working?

If snapshots still aren't created after `/sync`:

1. **Check bot logs** for any errors
2. **Test with CLI** (`run-once`) to isolate the issue
3. **Verify config** has snapshots enabled
4. **Check GitHub token** has write access
5. **Restart bot** to ensure latest code is loaded

**Remember:** Snapshots are created **silently** - there's no Discord message about snapshots. Check GitHub repository directly to verify they're being created.

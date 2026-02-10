# Quick Fix: Snapshots Not Created After /sync

## Problem
You run `/sync` in Discord but snapshots are not created in GitHub.

## Solution

### Step 1: Restart the Bot

Your bot was started on **Feb 9** and needs to be restarted to load the latest snapshot code:

```bash
# Stop the bot
pkill -f "python.*ghdcbot.*bot"

# Wait 2 seconds
sleep 2

# Start the bot again
nohup .venv/bin/python -m ghdcbot --config config/shubh-olrd.yaml bot > bot.log 2>&1 &

# Verify it's running
ps aux | grep "python.*ghdcbot.*bot" | grep -v grep
```

### Step 2: Verify Config Has Snapshots Enabled

Check `config/shubh-olrd.yaml`:

```yaml
snapshots:
  enabled: true  # Must be true
  repo_path: "shubham-orld/gitcord-data"
  branch: "main"
```

If missing, add it at the end of the file.

### Step 3: Test /sync Command

1. **Run `/sync` in Discord** (mentor role required)
2. **Wait for response:** Should say "✅ Sync complete!"
3. **Check bot logs:**
   ```bash
   tail -50 bot.log | grep -i snapshot
   ```
   Look for: `"GitHub snapshots written"`

4. **Check GitHub:**
   ```bash
   # Get latest snapshot
   curl -s -H "Authorization: Bearer YOUR_TOKEN" \
     https://api.github.com/repos/shubham-orld/gitcord-data/contents/snapshots | \
     jq -r '.[-1].name'
   ```
   
   Or visit: https://github.com/shubham-orld/gitcord-data/tree/main/snapshots

### Step 4: Verify Snapshot Was Created

After running `/sync`, you should see:
- ✅ Bot responds: "✅ Sync complete!"
- ✅ Bot logs show: "GitHub snapshots written"
- ✅ New snapshot directory in GitHub: `snapshots/YYYY-MM-DDTHH-MM-SS-xxxxx/`

## Important Notes

1. **Snapshots are created silently** - There's no Discord message about snapshots
2. **Check GitHub directly** - Snapshots appear in the repository, not in Discord
3. **One snapshot per `/sync`** - Each `/sync` creates a new timestamped snapshot
4. **Previous snapshots preserved** - Old snapshots are never overwritten

## If Still Not Working

1. **Check bot logs for errors:**
   ```bash
   tail -100 bot.log | grep -E "(error|exception|failed)" -i
   ```

2. **Test with CLI first:**
   ```bash
   python -m ghdcbot --config config/shubh-olrd.yaml run-once
   ```
   This should create a snapshot. If it does, the issue is with the bot. If it doesn't, check config.

3. **Verify GitHub token has write access:**
   ```bash
   curl -H "Authorization: Bearer YOUR_TOKEN" \
     https://api.github.com/repos/shubham-orld/gitcord-data
   ```
   Should return repository info, not 404 or 403.

## Expected Behavior

When `/sync` works correctly:

```
You: /sync
Bot: 🔄 Syncing GitHub events and sending notifications...
     ✅ Sync complete! Notifications sent for new GitHub events.

GitHub: New snapshot created at:
        snapshots/2026-02-10T18-00-00-abc12345/
```

The snapshot contains 7 JSON files:
- `meta.json`
- `identities.json`
- `scores.json`
- `contributors.json`
- `roles.json`
- `issue_requests.json`
- `notifications.json`

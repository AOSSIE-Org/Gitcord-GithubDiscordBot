# Snapshot Coverage Analysis

## What's Currently in Snapshots ✅

### 1. **meta.json**
- Schema version
- Generated timestamp
- Organization name
- Run ID
- Period start/end

### 2. **identities.json**
- Verified Discord ↔ GitHub identity mappings
- Format: `{discord_user_id, github_user}`

### 3. **scores.json**
- Current contributor scores
- Format: `{github_user, period_start, period_end, points}`

### 4. **contributors.json**
- Contribution summaries (aggregated)
- Format: `{github_user, issues_opened, prs_opened, prs_reviewed, comments, total_score}`

### 5. **roles.json**
- Discord member roles
- Format: `{discord_user_id, roles: []}`

### 6. **issue_requests.json**
- Pending issue assignment requests
- Format: `{request_id, discord_user_id, github_user, owner, repo, issue_number, status}`

### 7. **notifications.json**
- Recent sent notifications (last 1000)
- Format: `{dedupe_key, event_type, github_user, discord_user_id, repo, target, sent_at}`

---

## What's Missing from Snapshots ❌

### 1. **Raw Contribution Events** ⚠️ **IMPORTANT**
**SQLite Table:** `contributions`
**What it contains:**
- All GitHub events: `issue_opened`, `issue_closed`, `pr_opened`, `pr_merged`, `pr_reviewed`, `comment`, `issue_assigned`
- Full event payloads (JSON)
- Timestamps
- Repository information

**Why it's important:**
- **Full audit trail** - Complete history of all GitHub activity
- **Event details** - PR titles, issue titles, comment content, review states
- **Historical analysis** - Can reconstruct what happened over time
- **Org Explorer** - May need raw events for detailed visualization

**Current limitation:**
- Only **summaries** are in snapshots (`contributors.json`)
- **Raw events** are missing

---

### 2. **Audit Events** ⚠️ **USEFUL**
**SQLite:** `audit_events` (if implemented)
**What it contains:**
- All Gitcord actions: identity verification, role changes, issue assignments, etc.
- Actor information
- Timestamps
- Context data

**Why it's useful:**
- **Complete audit trail** of Gitcord operations
- **Debugging** - See what Gitcord did and when
- **Compliance** - Full record of all actions

**Status:** May not be implemented yet, but if it exists, should be in snapshots.

---

### 3. **Cursors** ⚠️ **LESS IMPORTANT**
**SQLite Table:** `cursors`
**What it contains:**
- Sync state (last processed timestamp per source)
- Used for incremental ingestion

**Why it's less important:**
- **Internal state** - Not needed for Org Explorer
- **Can be recomputed** - Can derive from contribution events
- **Not user-facing data**

**Recommendation:** Probably don't need in snapshots.

---

## Recommendations

### Priority 1: Add Raw Contribution Events 🔴 **HIGH PRIORITY**

**File:** `contributions.json` (new)

**Structure:**
```json
{
  "schema_version": "1.0.0",
  "generated_at": "2026-02-10T17:23:39+00:00",
  "org": "shubham-orld",
  "run_id": "abc123",
  "data": [
    {
      "github_user": "alice",
      "event_type": "pr_opened",
      "repo": "my-repo",
      "created_at": "2026-02-10T10:00:00+00:00",
      "payload": {
        "pr_number": 42,
        "pr_title": "Add feature X",
        "pr_url": "https://github.com/org/repo/pull/42",
        ...
      }
    },
    ...
  ]
}
```

**Why:**
- Complete event history
- Needed for detailed analysis
- Org Explorer may need this
- Can't reconstruct from summaries alone

**Size consideration:**
- Could be large (many events)
- Options:
  - Include all events in period
  - Include last N events
  - Include events since last snapshot

---

### Priority 2: Add Audit Events 🟡 **MEDIUM PRIORITY**

**File:** `audit_events.json` (new)

**Structure:**
```json
{
  "schema_version": "1.0.0",
  "generated_at": "2026-02-10T17:23:39+00:00",
  "org": "shubham-orld",
  "run_id": "abc123",
  "data": [
    {
      "actor_type": "discord_user",
      "actor_id": "123456789",
      "event_type": "identity_verified",
      "created_at": "2026-02-10T10:00:00+00:00",
      "context": {
        "github_user": "alice",
        "discord_user_id": "123456789"
      }
    },
    ...
  ]
}
```

**Why:**
- Complete audit trail of Gitcord actions
- Useful for debugging and compliance

---

### Priority 3: Keep Cursors Out 🟢 **LOW PRIORITY**

**Recommendation:** Don't add cursors to snapshots
- Internal state only
- Can be recomputed
- Not needed for Org Explorer

---

## Current Snapshot Completeness

### ✅ **Complete Coverage:**
- Identity mappings
- Current scores
- Contribution summaries
- Discord roles
- Issue requests
- Notifications

### ⚠️ **Partial Coverage:**
- Contributions (only summaries, not raw events)

### ❌ **Missing:**
- Raw contribution events (full history)
- Audit events (if implemented)

---

## For Org Explorer

**What Org Explorer likely needs:**

1. ✅ **Identities** - Who is linked
2. ✅ **Scores** - Current contributor scores
3. ✅ **Roles** - Discord roles
4. ⚠️ **Contributions** - Currently only summaries, may need raw events
5. ❌ **Raw Events** - Full event history (missing)

**Recommendation:** Add `contributions.json` with raw events for complete coverage.

---

## Implementation Priority

### Phase 1 (Current) ✅
- Meta, identities, scores, contributors (summaries), roles, issue_requests, notifications

### Phase 2 (Recommended) 🔴
- **Add `contributions.json`** with raw contribution events
- Include events from current period or last N events

### Phase 3 (Optional) 🟡
- Add `audit_events.json` if audit logging is implemented

---

## Summary

**Current Status:** Snapshots contain most important data, but **missing raw contribution events**.

**Recommendation:** Add raw contribution events to snapshots for complete coverage and Org Explorer compatibility.

**Impact:** Without raw events, you can't:
- See full event history
- Analyze specific PRs/issues
- Reconstruct detailed timeline
- Provide rich Org Explorer visualizations

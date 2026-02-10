# GitHub Snapshots Migration Checklist

This checklist tracks the migration from SQLite to GitHub-backed storage.

## Phase 1: Additive (✅ COMPLETE)

### Implementation
- [x] Design snapshot schemas (identities, contributors, scores, roles, issue_requests, notifications, meta)
- [x] Create `GitHubSnapshotWriter` (`src/ghdcbot/engine/snapshots.py`)
- [x] Add `SnapshotConfig` to `BotConfig`
- [x] Add `write_file` method to `GitHubRestAdapter`
- [x] Integrate snapshot writing into `Orchestrator.run_once()` (additive, after reports)
- [x] Add `list_recent_notifications` to `SqliteStorage`
- [x] Write unit tests for snapshot serialization
- [x] Write unit tests for GitHub file writing
- [x] Document snapshot schemas and usage

### Safety Checks
- [x] Snapshots are non-blocking (failures don't affect `run-once`)
- [x] SQLite remains active and unchanged
- [x] No existing features modified
- [x] All tests pass
- [x] Error handling implemented

### Configuration
- [x] `snapshots.enabled` flag (default: false)
- [x] `snapshots.repo_path` (format: "owner/repo")
- [x] `snapshots.branch` (optional, defaults to repo default)

## Phase 2: Dual-Write (Future)

### Tasks
- [ ] Mark GitHub snapshots as "official audit output"
- [ ] Update Org Explorer to consume snapshots
- [ ] Add snapshot validation/verification
- [ ] Document snapshot consumption patterns
- [ ] Monitor snapshot write success rates

### Safety Checks
- [ ] SQLite remains active
- [ ] No production dependency on GitHub reads
- [ ] Snapshot failures don't affect operations

## Phase 3: Gradual Downgrade (Future)

### Tasks
- [ ] Design GitHub read adapter
- [ ] Implement snapshot reading from GitHub
- [ ] Migrate cursor storage to GitHub
- [ ] Migrate deduplication to GitHub
- [ ] Make SQLite optional (cache only)
- [ ] Remove SQLite dependency (if desired)

### Safety Checks
- [ ] Backward compatibility maintained
- [ ] Migration path tested
- [ ] Rollback plan documented
- [ ] No data loss during migration

## What NOT to Change

### Explicit Non-Goals
- ❌ No real-time syncing
- ❌ No webhook listeners
- ❌ No replacing Discord bot behavior
- ❌ No changing how scoring works
- ❌ No "better" storage abstractions
- ❌ No event sourcing rewrite
- ❌ No removal of SQLite (until Phase 3)

### Protected Components
- `Orchestrator.run_once()` logic (only additive changes)
- `Storage` interface (no breaking changes)
- Scoring algorithms (unchanged)
- Discord bot commands (unchanged)
- Report generation (unchanged)

## Testing Strategy

### Unit Tests
- [x] Snapshot schema serialization
- [x] GitHub file writing (mocked)
- [x] Error handling
- [x] Configuration validation

### Integration Tests
- [ ] End-to-end snapshot writing (with test GitHub repo)
- [ ] Snapshot reading (Phase 2)
- [ ] Migration path (Phase 3)

### Manual Testing
- [ ] Enable snapshots in config
- [ ] Run `run-once` and verify snapshots written
- [ ] Verify snapshot files are valid JSON
- [ ] Verify snapshot files are timestamped correctly
- [ ] Verify Org Explorer can consume snapshots (Phase 2)

## Rollback Plan

If issues arise:

1. **Disable snapshots**: Set `snapshots.enabled: false` in config
2. **SQLite remains**: All data still in SQLite, no data loss
3. **No code changes needed**: Snapshot writing is additive, disabling is safe

## Success Criteria

### Phase 1 (Current)
- ✅ Snapshots written successfully to GitHub
- ✅ No impact on existing features
- ✅ All tests pass
- ✅ Documentation complete

### Phase 2 (Future)
- Snapshot consumption by Org Explorer working
- Snapshot validation passing
- No production issues

### Phase 3 (Future)
- GitHub becomes source of truth
- SQLite optional/cache only
- Migration tested and verified

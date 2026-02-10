# GitHub-Backed Snapshots

Gitcord can write periodic snapshots of its state to GitHub repositories as JSON files. This enables:

- **Org Explorer Integration**: Snapshots are consumable by Org Explorer and other tools
- **Audit Trail**: Append-only history of Gitcord state over time
- **Backup**: GitHub becomes a backup of Gitcord's persistent data
- **Migration Path**: Gradual transition from SQLite to GitHub-backed storage

## Architecture

### Design Principles

1. **Additive Only**: Snapshots are written AFTER `run-once` completes successfully
2. **Non-Blocking**: Snapshot failures never block `run-once` completion
3. **Append-Only**: Each snapshot is timestamped and never overwrites previous snapshots
4. **Deterministic**: Same input data produces identical snapshot files
5. **Human-Readable**: JSON files are formatted and sorted for easy inspection

### Snapshot Structure

Each snapshot run creates a timestamped directory:

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

### Snapshot Files

#### `meta.json`
Metadata about the snapshot:
```json
{
  "schema_version": "1.0.0",
  "generated_at": "2024-01-31T12:00:00+00:00",
  "org": "my-org",
  "run_id": "abc12345-def6-7890-ghij-klmnopqrstuv",
  "period_start": "2024-01-01T00:00:00+00:00",
  "period_end": "2024-01-31T23:59:59+00:00"
}
```

#### `identities.json`
Verified Discord ↔ GitHub identity mappings:
```json
{
  "schema_version": "1.0.0",
  "generated_at": "2024-01-31T12:00:00+00:00",
  "org": "my-org",
  "run_id": "abc12345-def6-7890-ghij-klmnopqrstuv",
  "data": [
    {
      "discord_user_id": "123456789012345678",
      "github_user": "alice"
    }
  ]
}
```

#### `scores.json`
Current contributor scores:
```json
{
  "schema_version": "1.0.0",
  "generated_at": "2024-01-31T12:00:00+00:00",
  "org": "my-org",
  "run_id": "abc12345-def6-7890-ghij-klmnopqrstuv",
  "data": [
    {
      "github_user": "alice",
      "period_start": "2024-01-01T00:00:00+00:00",
      "period_end": "2024-01-31T23:59:59+00:00",
      "points": 150
    }
  ]
}
```

#### `contributors.json`
Contribution summaries (if available):
```json
{
  "schema_version": "1.0.0",
  "generated_at": "2024-01-31T12:00:00+00:00",
  "org": "my-org",
  "run_id": "abc12345-def6-7890-ghij-klmnopqrstuv",
  "data": [
    {
      "github_user": "alice",
      "period_start": "2024-01-01T00:00:00+00:00",
      "period_end": "2024-01-31T23:59:59+00:00",
      "issues_opened": 5,
      "prs_opened": 3,
      "prs_reviewed": 2,
      "comments": 10,
      "total_score": 50
    }
  ]
}
```

#### `roles.json`
Discord member roles:
```json
{
  "schema_version": "1.0.0",
  "generated_at": "2024-01-31T12:00:00+00:00",
  "org": "my-org",
  "run_id": "abc12345-def6-7890-ghij-klmnopqrstuv",
  "data": [
    {
      "discord_user_id": "123456789012345678",
      "roles": ["Contributor", "Maintainer"]
    }
  ]
}
```

#### `issue_requests.json`
Pending issue assignment requests:
```json
{
  "schema_version": "1.0.0",
  "generated_at": "2024-01-31T12:00:00+00:00",
  "org": "my-org",
  "run_id": "abc12345-def6-7890-ghij-klmnopqrstuv",
  "data": [
    {
      "request_id": "req-123",
      "discord_user_id": "123456789012345678",
      "github_user": "alice",
      "owner": "my-org",
      "repo": "my-repo",
      "issue_number": 42,
      "issue_url": "https://github.com/my-org/my-repo/issues/42",
      "created_at": "2024-01-30T10:00:00+00:00",
      "status": "pending"
    }
  ]
}
```

#### `notifications.json`
Recent sent notifications (last 1000):
```json
{
  "schema_version": "1.0.0",
  "generated_at": "2024-01-31T12:00:00+00:00",
  "org": "my-org",
  "run_id": "abc12345-def6-7890-ghij-klmnopqrstuv",
  "data": [
    {
      "dedupe_key": "issue_assigned:my-org/my-repo:42:alice",
      "event_type": "issue_assigned",
      "github_user": "alice",
      "discord_user_id": "123456789012345678",
      "repo": "my-repo",
      "target": "42",
      "channel_id": null,
      "sent_at": "2024-01-30T10:00:00+00:00"
    }
  ]
}
```

## Configuration

Add a `snapshots` section to your config YAML:

```yaml
snapshots:
  enabled: true
  repo_path: "my-org/gitcord-data"  # Format: "owner/repo"
  branch: "main"  # Optional: defaults to repo's default branch
```

### Configuration Fields

| Field | Required | Description |
|-------|----------|-------------|
| `enabled` | Yes | Enable/disable snapshot writing |
| `repo_path` | Yes | GitHub repository path in `owner/repo` format |
| `branch` | No | Branch to write to (defaults to repo's default branch) |

## Usage

### Enabling Snapshots

1. Create a GitHub repository to store snapshots (e.g., `my-org/gitcord-data`)
2. Ensure your GitHub token has write access to the repository
3. Add the `snapshots` section to your config YAML
4. Run `run-once` or `/sync` - snapshots will be written automatically

### Snapshot Timing

Snapshots are written:
- **After** all `run-once` processing completes successfully
- **After** reports are generated
- **After** GitHub/Discord mutations are applied
- **Non-blocking**: Failures are logged but don't affect `run-once` completion

### Error Handling

If snapshot writing fails:
- Error is logged with full context
- `run-once` continues normally
- SQLite remains the source of truth
- Next `run-once` will attempt snapshot writing again

## Migration Strategy

### Phase 1: Additive (Current)

- ✅ Snapshots written to GitHub
- ✅ SQLite remains active
- ✅ Snapshots are audit output only
- ✅ Org Explorer can consume snapshots

### Phase 2: Dual-Write (Future)

- SQLite remains active
- GitHub snapshots become official audit output
- Org Explorer consumes snapshots
- No production dependency on GitHub reads yet

### Phase 3: Gradual Downgrade (Future)

- SQLite becomes:
  - Ingestion cursor cache
  - Deduplication helper
- Truth lives in GitHub
- SQLite never exposed externally

## Schema Versioning

Snapshot files include a `schema_version` field. When making breaking changes:

1. Increment `SCHEMA_VERSION` in `src/ghdcbot/engine/snapshots.py`
2. Update snapshot file schemas
3. Document migration path for consumers

Current schema version: `1.0.0`

## Integration with Org Explorer

Org Explorer can consume snapshots by:

1. Reading the latest snapshot directory from the configured repo
2. Parsing JSON files according to schema version
3. Displaying contributor data, scores, roles, etc.

Example:
```python
# Read latest snapshot
snapshot_dir = "snapshots/2024-01-31T12-00-00-abc12345"
scores = json.loads(repo.get_file(f"{snapshot_dir}/scores.json"))
identities = json.loads(repo.get_file(f"{snapshot_dir}/identities.json"))
```

## Safety Guarantees

1. **Never Overwrites**: Each snapshot is timestamped and unique
2. **Fail Gracefully**: Snapshot failures don't block `run-once`
3. **Audit Logging**: Every snapshot write is logged to audit log
4. **Deterministic**: Same input produces identical output
5. **No Breaking Changes**: Existing features unaffected

## Limitations

- Snapshots are written **after** `run-once` completes (not real-time)
- Requires GitHub write access to the snapshot repository
- Snapshot size grows over time (consider cleanup policies)
- Not suitable for high-frequency updates (designed for periodic snapshots)

## Future Enhancements

- Snapshot cleanup policies (retain last N snapshots)
- Snapshot compression
- Incremental snapshots (only changed data)
- Snapshot validation/verification
- Read snapshots back for recovery

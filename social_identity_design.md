# Social Identity Design – Week 5 Analysis

## Current Identity System Overview

### Database Schema (identity_links table)

```sql
CREATE TABLE identity_links (
    discord_user_id TEXT NOT NULL,
    github_user TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    verification_code TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    verified_at TEXT,
    unlinked_at TEXT,
    github_user_normalized TEXT,
    PRIMARY KEY (discord_user_id, github_user)
);

-- Indexes for fast lookups
CREATE UNIQUE INDEX idx_identity_links_discord_github_norm 
    ON identity_links (discord_user_id, github_user_normalized);
CREATE INDEX idx_identity_links_github_user 
    ON identity_links (github_user);
CREATE INDEX idx_identity_links_verified 
    ON identity_links (verified);
```

### Current Identity Flow

#### 1. **Link Phase** (/link command)
- Discord user provides GitHub username
- System generates verification code (6 characters)
- Code expires in 30 minutes
- User must add code to GitHub bio or gist
- Database: `identity_links` row created with `verified=0`, `verification_code` set, `expires_at` set

#### 2. **Verification Phase** (/verify-link command)
- User confirms they added verification code
- System checks GitHub bio + gists for code
- If found: `verified=1`, `verified_at=now`, `verification_code=NULL`, `expires_at=NULL`
- If not found: Checks if code expired, responds appropriately

#### 3. **Status Query** (/verify and /status commands)
- Returns current identity status (verified, pending, not_linked, verified_stale)
- Tracks staleness if max_age_days config set
- Read-only, no data modification

#### 4. **Unlink Phase** (/unlink command)
- Only verified identities can unlink
- Implements cooldown (default: 24 hours)
- Soft-delete: `verified=0`, `unlinked_at=now` (row never deleted)
- Audit trail preserved for compliance

### Storage Adapter Methods

**Current Identity Methods in SqliteStorage:**
```python
# Mutation operations
def create_identity_claim(discord_user_id, github_user, ttl_minutes) -> dict
def mark_identity_verified(discord_user_id, github_user) -> None
def unlink_identity(discord_user_id, cooldown_hours) -> dict | None
def insert_issue_request(...) -> None

# Query operations  
def get_identity_link(discord_user_id, github_user) -> dict | None
def get_identity_links_for_discord_user(discord_user_id) -> list[dict]
def get_identity_status(discord_user_id, max_age_days) -> dict
def list_verified_identity_mappings() -> list[IdentityMapping]
```

### IdentityLinkService (High-level API)

Located in: `src/ghdcbot/engine/identity.py` (or similar)

```python
class IdentityLinkService:
    def create_claim(discord_user_id, github_user, max_age_days) -> dict
    def verify_claim(discord_user_id, github_user) -> tuple[bool, str]
```

---

## Design Extension Points for Social Profiles

### 1. **Database Schema Extension**

Add new table: `social_profiles`

```sql
CREATE TABLE social_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id TEXT NOT NULL,
    platform TEXT NOT NULL,  -- 'x', 'linkedin', 'bluesky', etc.
    profile_handle TEXT NOT NULL,  -- Normalized username/URL
    display_value TEXT,  -- Full URL for display
    verified INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    
    UNIQUE(discord_user_id, platform),
    FOREIGN KEY(discord_user_id) REFERENCES identity_links(discord_user_id)
);
```

### 2. **Data Design**

Each Discord user can have:
- 1 GitHub (via identity_links) - **Required for verified status**
- 0 or 1 X/Twitter profile - **Optional**
- 0 or 1 LinkedIn profile - **Optional**
- Extendable to: Bluesky, Mastodon, Personal Website, etc.

### 3. **Validation Layer**

Need validators for each platform:

#### X/Twitter Validator
```python
class XProfileValidator:
    @staticmethod
    def validate(input_str: str) -> str | None:
        """
        Accept:
        - https://x.com/username
        - https://twitter.com/username
        - @username
        - username
        
        Return normalized username or raise ValueError
        """
```

#### LinkedIn Validator
```python
class LinkedInProfileValidator:
    @staticmethod
    def validate(input_str: str) -> str | None:
        """
        Accept:
        - https://linkedin.com/in/profile-id
        - https://www.linkedin.com/in/profile-id
        
        Store and return normalized URL
        Reject: Company pages, invalid formats
        """
```

### 4. **Service Layer Pattern**

New service: `SocialProfileService`

```python
class SocialProfileService:
    def __init__(self, storage):
        self.storage = storage
        self.validators = {
            'x': XProfileValidator,
            'linkedin': LinkedInProfileValidator,
        }
    
    async def set_profile(discord_user_id, platform, input_value) -> dict
    async def get_profiles(discord_user_id) -> dict
    async def remove_profile(discord_user_id, platform) -> bool
    async def validate_profile(platform, input_value) -> str
```

### 5. **Files to Modify/Create**

#### **New Files:**
- `src/ghdcbot/core/social_validators.py` - Platform validators
- `src/ghdcbot/engine/social_profiles.py` - SocialProfileService
- `src/ghdcbot/config/social_models.py` - Pydantic models for social profiles

#### **Modified Files:**
- `src/ghdcbot/adapters/storage/sqlite.py` - Add social_profiles table + CRUD methods
- `src/ghdcbot/core/models.py` - Add SocialProfile dataclass
- `src/ghdcbot/config/models.py` - Add social profile config options

#### **Future - Day 6 (Commands):**
- `src/ghdcbot/bot.py` - `/add-x`, `/add-linkedin`, `/remove-social`, `/my-profiles`

---

## Implementation Checklist

### Phase 1: Database & Storage (Today - Day 5)
- [ ] Add `social_profiles` table to schema
- [ ] Implement CRUD methods in SqliteStorage
- [ ] Test storage layer with SQLite

### Phase 2: Validators (Today - Day 5)  
- [ ] X/Twitter validator with tests
- [ ] LinkedIn validator with tests
- [ ] Extensible validator architecture

### Phase 3: Service Layer (Today - Day 5)
- [ ] Create SocialProfileService
- [ ] CRUD operations using storage
- [ ] Unit tests

### Phase 4: Discord Commands (Day 6)
- [ ] `/add-x` command with validator
- [ ] `/add-linkedin` command with validator
- [ ] `/remove-social` command
- [ ] `/my-profiles` query command
- [ ] Integration tests

---

## Key Design Decisions

### 1. **Soft Deletes Only**
- Never delete rows, only mark removed
- Preserves audit trail for compliance
- Consistent with existing identity_links pattern

### 2. **Platform Extensibility**
- Validators in separate classes per platform
- Easy to add new validators (Bluesky, Mastodon, Website, etc.)
- Registry pattern for validator lookup

### 3. **Optional Social Profiles**
- Social profiles are **optional** - users can have GitHub link without X/LinkedIn
- Unique constraint on (discord_user_id, platform) prevents duplicates
- Foreign key to identity_links keeps referential integrity

### 4. **Normalization**
- Store normalized username internally (lowercase for X)
- Store full URL for LinkedIn (immutable identifier)
- Display-friendly URLs for user messages

### 5. **No Auto-Verification**
- Unlike GitHub, X/LinkedIn profiles are not verified
- Possible future: webhook verification using X API/LinkedIn API
- For now: User provides, system normalizes and stores

---

## Integration with Existing Systems

### Identity System
- Social profiles are **supplementary** to GitHub identity
- User must have verified GitHub link first (prerequisite for Day 6 commands)
- Stored separately to avoid identity_links table bloat

### Notification System
- Could extend notifications to include social profile mentions
- Week 6+ feature: Notify via X/LinkedIn if set

### Role Assignment System
- Could use X/LinkedIn followers count for role qualification
- Week 6+ feature: Social score contributions

---

## Testing Strategy

### Unit Tests
```python
# test_social_validators.py
- test_x_validator_valid_username
- test_x_validator_valid_url
- test_x_validator_normalize
- test_x_validator_invalid
- test_linkedin_validator_valid_url
- test_linkedin_validator_invalid
- test_linkedin_validator_normalize

# test_social_storage.py
- test_set_x_profile
- test_set_linkedin_profile
- test_get_profiles
- test_remove_profile
- test_duplicate_profile_update
- test_list_all_profiles

# test_social_service.py
- test_add_profile_with_validation
- test_get_profile_for_user
- test_remove_profile
- test_error_handling
```

### Integration Tests
```python
# test_social_e2e.py
- test_set_and_retrieve_x_profile
- test_set_and_retrieve_linkedin_profile
- test_identity_prerequisite_check
- test_concurrent_profile_updates
```

---

## Future Extensions (Week 6+)

1. **Profile Verification**
   - Verify X handles via X API (OAuth)
   - Verify LinkedIn via LinkedIn API
   - Show "✓ Verified" badge on profiles

2. **Social Score**
   - Track X followers count
   - Track LinkedIn connections count
   - Include in role calculation

3. **Social Notifications**
   - Notify via X DMs for assigned issues
   - Share PR reviews to LinkedIn
   - Cross-platform activity broadcasting

4. **Social Discovery**
   - Public profile page with all socials
   - Find contributors by X handle
   - Team member social profiles

---

## Appendix: Current Schema

```
identity_links (existing):
├── discord_user_id (PK1)
├── github_user (PK2, normalized)
├── verified (0/1)
├── verification_code (temp)
├── expires_at (temp)
├── created_at
├── verified_at
├── unlinked_at
└── github_user_normalized

social_profiles (new):
├── id (PK)
├── discord_user_id (FK to identity_links)
├── platform (x, linkedin, etc.)
├── profile_handle (normalized)
├── display_value (user-friendly)
├── verified (0/1)
├── created_at
└── updated_at
```

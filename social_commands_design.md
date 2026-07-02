# Social Profile Commands Design

**Week 5 Day 6** – Discord slash command integration for contributor social profiles.

---

## Overview

Enable contributors to link their social profiles (X/Twitter, LinkedIn) through Discord commands. This extends the existing identity verification system (`/link`, `/verify-link`) to include optional social profiles.

**Key Design Goals:**
- User-friendly command interface (slash commands)
- Permission checks (users edit own profiles, admins view all)
- Validation feedback (invalid username, invalid URL)
- Seamless integration with existing `/status` command
- Backward compatible with existing identity flow

---

## Commands

### `/profile set x <username_or_url>`

**Purpose:** Link user's X (Twitter) profile.

**Parameters:**
- `username_or_url`: Username, @mention, or full URL

**Accepted Formats:**
- `@shubham5080` (with @mention)
- `shubham5080` (plain username)
- `https://x.com/shubham5080` (full URL)
- `https://twitter.com/shubham5080` (legacy Twitter URL)

**Behavior:**
1. Defer interaction (ephemeral)
2. Extract username from input using XProfileValidator
3. Call `service.set_profile(discord_user_id, "x", input_value)`
4. Display success embed with username

**Success Response:**
```
✅ X profile linked successfully.
Username: @shubham5080
```

**Error Responses:**
```
❌ Invalid X username.

Valid formats:
• @username
• username
• https://x.com/username
• https://twitter.com/username

Length: 1–15 characters, alphanumeric + underscore only.
```

---

### `/profile set linkedin <url>`

**Purpose:** Link user's LinkedIn profile.

**Parameters:**
- `url`: LinkedIn profile URL

**Accepted Formats:**
- `https://linkedin.com/in/shubham-shinde`
- `https://www.linkedin.com/in/shubham-shinde`
- `linkedin.com/in/shubham-shinde` (auto-add https)

**Behavior:**
1. Defer interaction (ephemeral)
2. Normalize and validate URL using LinkedInProfileValidator
3. Call `service.set_profile(discord_user_id, "linkedin", input_value)`
4. Display success embed with profile handle

**Success Response:**
```
✅ LinkedIn profile linked successfully.
Profile: linkedin.com/in/shubham-shinde
```

**Error Responses:**
```
❌ Invalid LinkedIn URL.

Valid formats:
• https://linkedin.com/in/profile-name
• https://www.linkedin.com/in/profile-name

Note: Company pages and school profiles are not supported for contributors.
```

---

### `/profile view`

**Purpose:** Display all linked social profiles for the user.

**Behavior:**
1. Defer interaction (ephemeral)
2. Call `service.get_profiles(discord_user_id)` to fetch all platforms
3. Build embed with list of linked profiles
4. Show action buttons (edit/remove) for each profile

**Response Structure:**
```
Embed Title: Your Social Profiles

Field: GitHub
Value: shubham5080 ✔️

Field: X
Value: @shubham5080 ✔️

Field: LinkedIn  
Value: linkedin.com/in/shubham-shinde ✔️

Field: Bluesky (Future)
Value: Not linked

Buttons: [Edit X] [Remove X] [Edit LinkedIn] [Remove LinkedIn]
```

**Empty State:**
```
No social profiles linked yet.

Link your profiles:
• /profile set x <username>
• /profile set linkedin <url>
```

---

### `/profile remove <platform>`

**Purpose:** Remove a linked social profile.

**Parameters:**
- `platform`: x | linkedin | bluesky | mastodon

**Behavior:**
1. Defer interaction (ephemeral)
2. Call `service.remove_profile(discord_user_id, platform)`
3. Return success or not-found response

**Success Response:**
```
✅ X profile removed successfully.
```

**Error Response (Not Found):**
```
❌ X profile not linked.

Current profiles:
• LinkedIn: linkedin.com/in/shubham-shinde
```

---

## `/status` Command Integration

**Current Behavior:**
```
Activity window: last 7 days (from bot config).
Linked GitHub: shubham5080.
Your roles: Contributor, Member.
```

**New Behavior (Extended):**
```
Activity window: last 7 days (from bot config).
Linked GitHub: shubham5080 ✔️
Social Profiles:
  • X: @shubham5080
  • LinkedIn: linkedin.com/in/shubham-shinde
Your roles: Contributor, Member.
```

**If Not Linked:**
```
Social Profiles: Not linked yet. Use `/profile set x` or `/profile set linkedin`.
```

---

## Permission Model

| Action | User | Mentor/Admin |
|--------|------|-------------|
| Set own profile | ✅ | ✅ |
| View own profile | ✅ | ✅ |
| Remove own profile | ✅ | ✅ |
| View others' profiles | ❌ | ✅ |
| Edit others' profiles | ❌ | ❌ |

**Implementation:**
```python
def can_edit_profile(user_id: str, target_user_id: str, is_admin: bool) -> bool:
    """Check if user can edit target profile."""
    if user_id == target_user_id:
        return True  # Can edit own
    return False  # No cross-user edits, even for admins
```

---

## Error Handling

| Error | Message | Cause |
|-------|---------|-------|
| InvalidUsername | "Invalid X username" | Username doesn't match regex |
| InvalidURL | "Invalid LinkedIn URL" | URL is malformed |
| DuplicateProfile | "X profile already linked" | Platform already set |
| NotFound | "X profile not linked" | Trying to remove non-existent |
| PermissionDenied | "Cannot edit other users' profiles" | User lacks permission |
| StorageError | "Database error. Please try again." | SQLite CRUD failure |

---

## Validation Rules

### X/Twitter (@)
- Length: 1–15 characters
- Allowed: a-z, 0-9, underscore only
- Case-insensitive storage
- Supported input: `@user`, `user`, `https://x.com/user`, `https://twitter.com/user`

### LinkedIn
- Must contain `/in/` path segment
- Company pages rejected (contain `/company/`)
- School profiles rejected (contain `/school/`)
- URL normalized: `https://www.linkedin.com/in/...` → `https://linkedin.com/in/...`
- Trailing slashes and query params removed

---

## Code Structure

### Files to Create/Modify

**New File:** `src/ghdcbot/adapters/discord/social_commands.py`
```python
# Command handlers for /profile set x, /profile set linkedin, etc.
# Separate from main bot.py for maintainability
```

**Modified:** `src/ghdcbot/bot.py`
```python
# Import social_commands
# Extend /status command
# Add permission checks helper
```

**New File:** `tests/test_social_commands.py`
```python
# Tests for /profile set, /profile view, /profile remove, /status integration
```

### Design Patterns

**1. Validation Preview**
Before storing, show user what will be saved:
```python
# In /profile set x:
preview = await service.validate_profile("x", user_input)
# preview = {"normalized": "@shubham5080", "display": "@shubham5080"}
await interaction.followup.send(f"Will link: {preview['display']}")
```

**2. Ephemeral Responses**
All responses are ephemeral (private) to reduce channel noise:
```python
await interaction.response.defer(ephemeral=True)
```

**3. Async/Await Pattern**
All service calls use async/await:
```python
profile = await asyncio.to_thread(service.set_profile, ...)
```

---

## Integration Points

**Service Layer:**
- `SocialProfileService.set_profile()` – Store profile
- `SocialProfileService.get_profiles()` – List all profiles
- `SocialProfileService.remove_profile()` – Delete profile
- `SocialProfileService.validate_profile()` – Validation without storing

**Storage Layer:**
- `SQLiteStorage.set_social_profile()` – CRUD insert/update
- `SQLiteStorage.get_all_social_profiles()` – Fetch user's all profiles
- `SQLiteStorage.remove_social_profile()` – Delete profile

**Discord Layer:**
- `discord.Interaction` – Command context
- `discord.app_commands.describe()` – Parameter docs
- `discord.Embed` – Rich response formatting
- `discord.ui.View` – Interactive buttons (future: edit/remove in UI)

---

## Response Formatting

### Success Embed

```python
embed = discord.Embed(
    title="✅ X profile linked",
    description="Your X profile has been linked successfully.",
    color=discord.Color.green()
)
embed.add_field(name="Username", value="@shubham5080", inline=False)
embed.set_footer(text="Use /profile view to see all profiles")
```

### Error Embed

```python
embed = discord.Embed(
    title="❌ Invalid X username",
    description="Please check the format.",
    color=discord.Color.red()
)
embed.add_field(
    name="Valid formats",
    value="• @username\n• username\n• https://x.com/username",
    inline=False
)
```

### Profile List Embed

```python
embed = discord.Embed(
    title="Your Social Profiles",
    color=discord.Color.blue()
)
embed.add_field(name="GitHub", value="shubham5080 ✔️", inline=False)
embed.add_field(name="X", value="@shubham5080 ✔️", inline=False)
embed.add_field(name="LinkedIn", value="linkedin.com/in/shubham-shinde ✔️", inline=False)
```

---

## Testing Strategy

### Unit Tests (test_social_commands.py)

1. **Command Parsing**
   - `/profile set x @username` → username extracted
   - `/profile set x user@123` → invalid chars rejected
   - `/profile set linkedin invalid-url` → URL validation

2. **Service Integration**
   - Mock `SocialProfileService`
   - Verify `set_profile()` called with correct args
   - Verify response message contains expected data

3. **Permission Checks**
   - User can edit own profile
   - User cannot edit others' profiles
   - Admin cannot edit others' profiles

4. **Status Integration**
   - `/status` shows linked profiles
   - `/status` shows "Not linked" if empty
   - Existing GitHub link still displayed

5. **Error Cases**
   - Invalid input → error embed sent
   - Storage error → generic error message
   - Platform not found → clear error

### Integration Tests

- Test full flow: set X → view → verify in /status → remove
- Test multiple users isolation
- Test concurrent updates

### Manual Discord Testing

- [ ] Set X with various formats
- [ ] Set LinkedIn URL
- [ ] View profiles
- [ ] Remove profile  
- [ ] Check /status shows profiles
- [ ] Verify existing /link flow unchanged
- [ ] Test error messages

---

## Future Enhancements

1. **Edit Buttons in UI**
   - `/profile view` shows [Edit] and [Remove] buttons
   - Clicking opens modal for edit

2. **Verification**
   - Optional: Verify X profile is real (API call)
   - Optional: Verify LinkedIn profile public

3. **Profile Cards**
   - `/profile view @user` – View contributor's social profiles (mentor-only)
   - Display in contributor cards

4. **Additional Platforms**
   - Bluesky: `bluesky.social/profile/user`
   - Mastodon: instance-specific, validate domain
   - Personal website: validate URL scheme

---

## Completion Checklist

- [x] Design document complete
- [ ] Commands implemented in `social_commands.py`
- [ ] `/profile set x` working
- [ ] `/profile set linkedin` working
- [ ] `/profile remove` working
- [ ] `/profile view` working
- [ ] `/status` extended to show profiles
- [ ] Permission checks added
- [ ] Tests written and passing
- [ ] Documentation updated
- [ ] Manual Discord testing done
- [ ] Full test suite passing (294+ tests)

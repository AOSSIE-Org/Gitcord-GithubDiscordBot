# Tests Overview

Status: All tests passing (last run: pytest, 13 tests, 0.26s).

## Test Coverage

- `tests/test_config.py`: validates config parsing; fails if `role_mappings` is missing/empty.
- `tests/test_empty_org_behavior.py`: if an org has no repos, it logs a clear message and plans remain empty.
- `tests/test_mutation_policy_gating.py`: writers skip API calls in dry-run/observer/disabled-write modes.
- `tests/test_planning_determinism.py`: role/assignment planning is deterministic and ordered consistently.
- `tests/test_repo_filtering.py`: repo allow/deny filtering works and warns when everything is filtered.
- `tests/test_role_planning_correctness.py`: role planning adds/removes/no-ops correctly based on scores.
- `tests/test_user_repo_fallback.py`: falls back to user repos on org unauthorized, with correct log message.
- `tests/test_writer_safety.py`: writers don’t call APIs when plan lists are empty.

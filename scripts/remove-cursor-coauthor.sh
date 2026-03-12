#!/bin/bash
# Removes "Co-authored-by: Cursor <cursoragent@cursor.com>" from all commit messages.
# Run from repo root. Backup is created as refs/original/refs/heads/main (and others).

set -e
cd "$(git rev-parse --show-toplevel)"

echo "=== Backup: creating backup-main branch (current state) ==="
if git rev-parse --verify backup-main >/dev/null 2>&1; then
  echo "WARNING: backup-main already exists. Delete it first or rename if you want a fresh backup."
  exit 1
fi
git branch backup-main

# Optionally backup all branches:
echo "=== Creating backup refs for all branches ==="
for branch in $(git for-each-ref --format='%(refname:short)' refs/heads/); do
  git branch "backup-$branch" "$branch" 2>/dev/null || true
done

echo "=== Rewriting all commit messages to remove Cursor co-author line ==="
git filter-branch -f --msg-filter 'sed "/^Co-authored-by: Cursor <cursoragent@cursor.com>$/d"' -- --all

echo "=== Done. Cursor co-author line removed from all commits. ==="
echo ""
echo "Next steps:"
echo "  1. Check: git log --oneline -5  (messages should not contain Co-authored-by: Cursor)"
echo "  2. Force-push to update remote: git push --force-with-lease origin main"
echo "  3. If you had other branches, force-push them too."
echo "  4. To restore backup if needed: git reset --hard backup-main"
echo "  5. Remove backup refs (optional, after you're happy): git for-each-ref --format='delete %(refname)' refs/original | git update-ref --stdin"

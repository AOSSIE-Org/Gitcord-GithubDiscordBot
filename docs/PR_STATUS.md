# PR status repository suggestions

The `/pr-status` `repo` option discovers organization repositories accessible to
Gitcord's GitHub credentials and respects the configured repository allow/deny
filter. Repository names are matched case-insensitively, with prefix matches
shown before substring matches. Discord shows at most 25 choices; keep typing
to narrow the list for larger organizations.

The REST adapter caches repository discovery for 60 seconds. If discovery fails
or takes longer than 1.5 seconds, autocomplete falls back to permitted repository
names in the local configuration. A repository does not need an existing PR or a
notification channel mapping to appear when the repository filter permits it.

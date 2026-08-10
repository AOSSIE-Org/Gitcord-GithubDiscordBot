# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| `main` (latest) | ✅ |
| Tagged releases (e.g. `v0.1.0`) | ✅ best-effort |

## Reporting a vulnerability

Please **do not** open a public GitHub Issue for security vulnerabilities.

1. Email the maintainers listed in [`MAINTAINERS.md`](MAINTAINERS.md), or
2. Use [GitHub private vulnerability reporting](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/security/advisories/new) if enabled for this repository.

Include:

- Description of the issue and impact
- Steps to reproduce
- Affected version / commit if known

We aim to acknowledge reports within **14 days**.

## Scope notes

Gitcord handles Discord and GitHub tokens and may post messages or assign reviews when configured. Treat `.env`, App private keys, and SQLite identity data as sensitive. Never commit secrets.

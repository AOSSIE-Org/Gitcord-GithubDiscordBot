# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| `main` (latest) | ✅ |
| Tagged releases (e.g. `v0.1.0`) | ✅ best-effort |

## Reporting a vulnerability

Please **do not** open a public GitHub Issue for security vulnerabilities.

Use one of these private channels:

1. Email the maintainer: **shubhamshinde5080@gmail.com**
2. [GitHub private vulnerability reporting](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/security/advisories/new) (preferred when available)

You may also contact mentors listed in [`MAINTAINERS.md`](MAINTAINERS.md) via Discord for triage, but email or GitHub private reporting is the guaranteed path for sensitive details.

Include:

- Description of the issue and impact
- Steps to reproduce
- Affected version / commit if known

We aim to acknowledge reports within **14 days**.

## Scope notes

Gitcord handles Discord and GitHub tokens and may post messages or assign reviews when configured. Treat `.env`, App private keys, and SQLite identity data as sensitive.

Never commit secrets, credentials, live tokens, production SQLite databases, or unredacted identity data. Provide tokens through `.env` files or environment-variable expansion instead.

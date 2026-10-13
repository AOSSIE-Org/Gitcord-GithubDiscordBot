# Gitcord — Local handover

**Start here:** [`HANDOVER-EASY.txt`](../HANDOVER-EASY.txt) (plain language).

**One tool:** `./scripts/gitcord-handover`

| Who | Command |
| --- | --- |
| Old PC | `./scripts/gitcord-handover pack [--encrypt]` → one file on Desktop (`.tar.gz` or `.tar.gz.gpg`) |
| New PC | `./scripts/gitcord-handover restore /path/to/that-file` (auto-detects encryption) |
| Old PC after move | `./scripts/gitcord-handover stop-old` |
| Anyone | `./scripts/gitcord-handover check` |

**AI:** paste [`HANDOVER_AI_PROMPT.md`](HANDOVER_AI_PROMPT.md) into Cursor and give the archive path.

## Rules & Security

- Archive contains sensitive credentials (`.env` tokens, GitHub App private key, database).
- Without `--encrypt`, secrets are stored in plaintext. Only transfer over a trusted channel (e.g. secure USB).
- With `--encrypt`, secrets are encrypted with AES-256 (passphrase-protected). Keep the passphrase separate from the archive file.
- Delete the archive from both machines after `restore` is complete and verified.
- Do not commit or upload the archive (`.tar.gz` or `.tar.gz.gpg`) to public GitHub or untrusted storage.
- Do not run bots on two PCs with the same Discord token.
- Keep AOSSIE and Stability Nexus separate (the script already does).

## Details

Docker background: [DOCKER.md](DOCKER.md). Safety: [AGENTS.md](../AGENTS.md).

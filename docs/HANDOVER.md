# Gitcord — Local handover

**Start here:** [`HANDOVER-EASY.txt`](../HANDOVER-EASY.txt) (plain language).

**One tool:** `./scripts/gitcord-handover`

| Who | Command |
| --- | --- |
| Old PC | `./scripts/gitcord-handover pack` → one `.tar.gz` on Desktop |
| New PC | `./scripts/gitcord-handover restore /path/to/that.tar.gz` |
| Old PC after move | `./scripts/gitcord-handover stop-old` |
| Anyone | `./scripts/gitcord-handover check` |

**AI:** paste [`HANDOVER_AI_PROMPT.md`](HANDOVER_AI_PROMPT.md) into Cursor and give the `.tar.gz` path.

### Rules
- Do not commit the `.tar.gz` or `.env` / `.pem`
- Do not run bots on two PCs with the same Discord token
- Keep AOSSIE and Stability Nexus separate (the script already does)

### Details
Docker background: [DOCKER.md](DOCKER.md). Safety: [AGENTS.md](../AGENTS.md).

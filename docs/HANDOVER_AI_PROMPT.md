# AI prompt — easy Gitcord handover

Paste into Cursor on the **new** PC (inside this repo). Human cheat sheet: [`HANDOVER-EASY.txt`](../HANDOVER-EASY.txt).

---

```text
Help me install the live Gitcord bots on this PC from ONE handover file.

Read HANDOVER-EASY.txt and docs/HANDOVER.md.

I will give you the path to: gitcord-handover-*.tar.gz

Do exactly:
1. Confirm Docker is installed and running.
2. Ask me whether replacing existing .env/config/volumes is OK.
3. From repo root run (only add --force after I explicitly approve replace):
   ./scripts/gitcord-handover restore /ABS/PATH/TO/gitcord-handover-*.tar.gz
4. Run: ./scripts/gitcord-handover check
5. Tell me to test in Discord: /profile and /who-is (AOSSIE + Stability Nexus).
6. After I confirm OK, tell me to run on the OLD PC:
   ./scripts/gitcord-handover stop-old

Rules:
- Never commit .env, .pem, .tar.gz, or databases.
- Never invent tokens or skip restore (empty DB is wrong).
- Never mix AOSSIE and Stability Nexus.
- Warn if old PC might still be running the same Discord tokens.
- Never pass --force unless I explicitly confirm wipe/replace.
- Explain in simple language.
- If pack is needed on old PC: ./scripts/gitcord-handover pack
  (pack briefly stops bots for a consistent DB snapshot, then restarts them)
```

---

Short:

```text
Run ./scripts/gitcord-handover restore <my.tar.gz> per HANDOVER-EASY.txt.
Then check, Discord smoke-test, then stop-old on the previous PC. No secrets in git.
```

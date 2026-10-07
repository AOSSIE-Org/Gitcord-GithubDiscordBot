# AI prompt — easy Gitcord handover

Paste into Cursor on the **new** PC (inside this repo). Human cheat sheet: [`HANDOVER-EASY.txt`](../HANDOVER-EASY.txt).

---

```text
Help me install the live Gitcord bots on this PC from ONE handover file.

Read HANDOVER-EASY.txt and docs/HANDOVER.md.

I will give you the path to the handover archive:
either gitcord-handover-*.tar.gz (plain) or gitcord-handover-*.tar.gz.gpg (encrypted).

Do exactly:
1. Confirm Docker is installed and running.
2. Ask me whether replacing existing .env/config/volumes is OK.
3. From repo root run (only add --force after I explicitly approve replace):
   ./scripts/gitcord-handover restore /ABS/PATH/TO/ARCHIVE
4. Run: ./scripts/gitcord-handover check
5. Tell me to test in Discord: /profile and /who-is (AOSSIE + Stability Nexus).
6. After I confirm OK, tell me to run on the OLD PC:
   ./scripts/gitcord-handover stop-old

Rules:
- Never commit .env, .pem, handover archives (.tar.gz / .tar.gz.gpg), or databases.
- Never invent tokens or skip restore (empty DB is wrong).
- Never mix AOSSIE and Stability Nexus.
- Warn if old PC might still be running the same Discord tokens.
- Never pass --force unless I explicitly confirm wipe/replace.
- For encrypted archives (.gpg), restore auto-detects encryption and gpg prompts for the passphrase directly. Never ask me to paste the passphrase into the chat, never put it on a command line, and let me enter it into the gpg prompt myself.
- Remind me to delete the archive from both computers after restore is verified.
- Explain in simple language.
- If pack is needed on old PC: ./scripts/gitcord-handover pack (or ./scripts/gitcord-handover pack --encrypt)
  (pack briefly stops bots for a consistent DB snapshot, then restarts them)
```

---

Short:

```text
Run ./scripts/gitcord-handover restore <archive-path> per HANDOVER-EASY.txt.
Then check, Discord smoke-test, then stop-old on the previous PC. Delete archive after restore. No secrets in git.
```

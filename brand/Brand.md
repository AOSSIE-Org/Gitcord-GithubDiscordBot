# Gitcord — Brand & Design Specifications

Official branding for **Gitcord**, AOSSIE’s Discord ↔ GitHub automation engine (GSoC 2026).

---

## Brand identity

**Gitcord** bridges GitHub organization activity and Discord communities: verified identity linking, contributor profiles, PR/issue notifications, and safe (audit-first) sync.

Tone: clear, trustworthy, mentor-friendly — open-source tooling, not consumer social.

---

## Logo & assets

All brand assets live in [`brand/`](./):

| Asset | Path | Use |
| ----- | ---- | --- |
| Primary logo (SVG) | [`logo.svg`](./logo.svg) | README, docs, light/dark pages |
| Favicon / logomark (SVG) | [`favicon.svg`](./favicon.svg) | Browser tabs, small marks |
| Discord App / Bot icon | [`icons/discord-app-icon.png`](./icons/discord-app-icon.png) | Discord Developer Portal |
| GitHub App icon | [`icons/github-app-icon.png`](./icons/github-app-icon.png) | GitHub App marketplace / install |
| Logo on white | [`icons/logo-white-bg.png`](./icons/logo-white-bg.png) | Light backgrounds, slides |

Canonical copies also ship under [`public/`](../public/) for README and install docs.

### Usage rules

- Prefer SVG for web and docs; use PNG icons for Discord/GitHub portals (size requirements).
- Keep aspect ratio; do not stretch.
- On dark UIs, prefer the primary SVG or dark GitHub App asset.
- Do not place the mark on busy photos without a solid backing plate.

---

## Color palette

Inspired by Discord blurple + GitHub dark, with AOSSIE green accents used in badges.

| Token | Hex | Usage |
| ----- | --- | ----- |
| **Discord Blurple** | `#5865F2` | Discord-side accent, primary CTA |
| **GitHub Dark** | `#0D1117` | Dark backgrounds, terminal/docs chrome |
| **GitHub Green** | `#238636` | Success, “synced/active”, checkmarks |
| **Surface** | `#161B22` | Cards / panels on dark |
| **Text Light** | `#F0F6FC` | Text on dark |
| **Text Muted** | `#8B949E` | Secondary labels |
| **AOSSIE Green** | `#228B22` | Org badge / secondary brand link |
| **AOSSIE Gold** | `#FFC517` | Badge label accents |

```css
:root {
  --gitcord-blurple: #5865f2;
  --gitcord-github-dark: #0d1117;
  --gitcord-github-green: #238636;
  --gitcord-surface: #161b22;
  --gitcord-text: #f0f6fc;
  --gitcord-muted: #8b949e;
  --aossie-green: #228b22;
  --aossie-gold: #ffc517;
}
```

---

## Typography

Gitcord is a developer tool. Prefer readable system / open sans-serif stacks (not decorative display fonts in product UI).

| Role | Recommendation |
| ---- | ---------------- |
| **UI / Discord embeds** | Discord client defaults (do not override) |
| **Docs / README** | System UI stack: `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` |
| **Code / config / logs** | Monospace: `ui-monospace, "SFMono-Regular", Consolas, "Liberation Mono", monospace` |
| **Marketing slides** | Clean sans (e.g. Inter or system UI); keep titles short |

Hierarchy for slides/posts:

- Title: bold, high contrast on dark navy/charcoal
- Body: regular weight, muted secondary where needed
- Avoid dense walls of hashtags or emoji clusters

---

## Related links

- Repo: https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot  
- AOSSIE Discord (Gitcord project thread): https://discord.com/channels/1022871757289422898/1465995983791063140  
- Install icons: see `INSTALLATION.md` Step 2.2  

# OpenArc — Lightweight Open-Source Static Games Hub

A **fully static** site that catalogs and serves lightweight open-source HTML5 games.
No backend, no database, no runtime build. Optimized for **SEO** (crawlers) and
**GEO** (Generative Engine Optimization), with accessibility and load performance
built in.

---

## 🔐 Security: how credentials are handled

**No secret is ever stored in this repository.** Deploy scripts read credentials
**only from environment variables** (or CI secrets). They never accept secrets as
CLI arguments and never print them.

- `.env.example` lists variable **names only** — fill it in locally, never commit it.
- `.gitignore` excludes `dist/`, `.env` and `__pycache__`.
- CI references `${{ secrets.* }}` exclusively (`.github/workflows/deploy.yml`).
- `scripts/fetch-games.py` reads `ITCH_IO_API_KEY` from the environment and works
  without it.

> **If you have pasted API tokens into a chat, issue, or ticket, treat them as
> compromised and rotate them immediately** (Cloudflare ▸ API Tokens / R2 keys;
> GitHub ▸ Personal access tokens; Neon ▸ API keys; Redis Cloud ▸ database password).
> Full policy: [`SECURITY.md`](SECURITY.md).

**The site needs no backend.** There is no database, Redis, serverless function or
LLM API call at runtime, which is exactly why it hosts with **zero credentials**.
Adding such services would require a server component plus runtime secrets and would
break the static-hosting requirement; keep any dynamic work in a separate service
that writes committed files into `data/` at build time.

---

## Build & deploy (production)

```bash
python scripts/build.py        # pre-render -> dist/
python scripts/check-seo.py    # validate meta / JSON-LD / crawlability / a11y
python scripts/deploy.py       # dry run (no credentials needed)
```

Deploy `dist/` to any static host — it is just 20 files, ~116 KB.

Every target below reads credentials **only from environment variables / CI
secrets**. Nothing sensitive is stored in the repo, and no secret is ever printed.

### Cloudflare Pages (recommended)
`wrangler.toml` is included (`pages_build_output_dir = "dist"`), and `_headers` /
`_redirects` ship the same security + cache headers and 301s that `netlify.toml`
provides on Netlify (**netlify.toml is ignored by Pages**).
```bash
export CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=...   # or: npx wrangler login
python scripts/build.py
npx wrangler@latest pages deploy dist --project-name=openarc
```

### Cloudflare R2 (S3-compatible bucket)
```bash
export R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=...
pip install -r requirements-deploy.txt
python scripts/deploy.py --target r2
```
Missing variables are reported **by name only**, and the script falls back to a
safe dry run. Content types and `Cache-Control` are set per file.

### Cloudflare Pages
```bash
export CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=...
python scripts/build.py
npx wrangler@latest pages deploy dist --project-name=openarc
```

### GitHub Pages
Uses the official `actions/deploy-pages` flow with the built-in `GITHUB_TOKEN` —
**no personal access token required**. Set *Settings ▸ Pages ▸ Source* to
**GitHub Actions**, then push to `main` (`.github/workflows/pages.yml`).

For a **project site** (`https://user.github.io/<repo>/`) the internal absolute
paths must be prefixed, otherwise every asset 404s:
```bash
python scripts/build.py --base /<repo>/ --site-url https://user.github.io/<repo>
```
With a **custom domain**, use `--base / --site-url https://your-domain --cname your-domain`.
The build also emits `.nojekyll` (stops Jekyll from mangling output) and `CNAME`.

Local equivalent:
```bash
python scripts/build.py --base /static-games-hub/ && python -m http.server 8080 --directory dist
```

### Netlify / Vercel / S3 / nginx
Point the host at `dist/` (Netlify is pre-configured via `netlify.toml`:
`command = python scripts/build.py`, `publish = "dist"`).

### Local preview
```bash
python -m http.server 8080 --directory dist
```

---

## Why `build.py` exists (the key SEO fix)

Rendering the catalog with client-side JavaScript meant crawlers that skip JS could
see an **empty** list. `build.py` pre-renders `data/games.json` into real `<li>`
elements, so shipped HTML already contains every title, description and link.
`main.js` is now **progressive enhancement only** (filter/search over existing items),
`<noscript>` keeps the page usable, and `check-seo.py` fails the build if the list
is not pre-rendered.

---

## SEO checklist (implemented)

| Area | Implemented |
|------|-------------|
| Core meta | `title`, `description` (≤160), `keywords`, `canonical`, `robots`, `viewport`, `theme-color`, `color-scheme`, `googlebot` |
| Open Graph | `og:type/site_name/locale/title/description/url/image/image:width/image:height/image:alt` |
| Twitter Card | `summary_large_image` + `site/creator/title/description/image/image:alt` |
| Structured data | `Organization`, `WebSite`+`SearchAction`, `BreadcrumbList`, `ItemList`, `FAQPage`, `HowTo`, `VideoGame` — game pages carry `VideoGame` + `HowTo` + `BreadcrumbList` |
| Crawlability | pre-rendered catalog, `sitemap.xml` (auto `lastmod`), `robots.txt`, `<noscript>`, `/data/games.json` |
| Semantics | `header/nav/main/article/section/footer`, single `h1`, logical `h2/h3`, `<table>` with `caption`+`scope`, `<dl>` source breakdown, `aria-label`s |
| Listing page | `ItemList` schema for the whole catalog + a "Catalog by source" `<dl>` section |

### Non-thin game pages (content injection)

Game pages used to be a board plus one line of copy — very little to index. Now each
game carries unique **How to play / Controls / Tips** copy that lives in
`data/games.json` (`howto`, `controls`, `tips`) and is injected at build time by
`build.py`. Measured body copy: homepage 807 words, listing 282, each game page
86–102. `check-seo.py` fails the build if any page falls below its word floor.

### What `check-seo.py` enforces (CI gate)

Beyond meta tags it fails the build on:
- **duplicate `<title>` / meta description** across pages (they compete in SERPs);
- **skipped heading levels** (e.g. `h1 → h3`), which breaks screen-reader navigation;
- **broken internal links** — every `href="/…"` must resolve to a file in `dist/`
  (base-path aware, so GitHub Pages sub-path builds are covered too);
- **`og:image` that does not resolve** to a real file;
- **thin content** — per-page minimum word counts;
- **missing sitemap entries** and a missing/short `llms.txt`;
- **catalog not pre-rendered** (the JS-only regression guard).

## GEO checklist (implemented)

- **`llms.txt`** (auto-generated each build): a compact, high-signal plain-text
  summary — key facts, per-game list, FAQ answers — for AI crawlers.
- **`robots.txt` explicitly allows** `GPTBot`, `OAI-SearchBot`, `ChatGPT-User`,
  `PerplexityBot`, `ClaudeBot`, `anthropic-ai`, `Google-Extended`, `Applebot-Extended`.
- **"Key facts at a glance"** block — extractable bullets an AI can quote verbatim.
- **Definition-first copy**: "OpenArc is a free, fully static hub of …".
- **FAQ** + `FAQPage` schema; **HowTo** schema for self-host steps.
- **Source comparison table** with caption — easy to turn into a table answer.
- No trackers, no paywalls, no JS-gated content — answers stay reproducible.

## Accessibility & performance

- Skip link, `<main>` landmark, `:focus-visible`, contrast ≥ 8:1 (AAA),
  `prefers-reduced-motion`, `[hidden]` enforcement, keyboard-operable controls.
- Zero web fonts, zero external requests, no layout-shifting media.
- CSS preloaded, JS `defer`, OG image 3.3 KB, whole site ≈ 116 KB.
- **Build-time HTML minification** (95 KB → 88 KB of HTML, −8%), with
  `<script>` / `<style>` / `<pre>` contents explicitly protected and `--no-minify`
  available for debugging.
- Security + cache headers in `netlify.toml`.

---

## Structure

```
static-games-hub/
├── index.html / games.html / 404.html   # templates (placeholders filled by build)
├── wrangler.toml                  # Cloudflare Pages config (publish dir = dist)
├── play/                          # 7 self-hosted games (MIT, dependency-free)
│     snake  tic-tac-toe  2048  memory  breakout  pong  minesweeper
├── slots/                         # Blender-MCP slot machine SPA (index.html/app.js/styles.css)
├── assets/ css/ js/ img/cover.png
├── data/games.json                # single source of truth (14 entries)
├── _headers  _redirects           # Cloudflare Pages headers + 301s
├── scripts/
│   ├── build.py        # pre-render -> dist/ (+ llms.txt, sitemap.xml, game copy)
│   ├── check-seo.py    # SEO/GEO/a11y validator (CI-friendly)
│   ├── deploy.py       # R2/S3 upload (env creds) + dry run
│   ├── fetch-games.py  # refresh catalog (env token, optional)
│   └── make-cover.py   # stdlib-only OG image generator
├── .github/workflows/
│     deploy.yml  # build + validate + R2 / Cloudflare Pages (repo secrets)
│     pages.yml   # GitHub Pages (GITHUB_TOKEN, no PAT needed)
├── .env.example  .gitignore  requirements-deploy.txt
├── netlify.toml  robots.txt
└── dist/                          # ← deploy this (generated)
      + llms.txt, sitemap.xml      #   generated from data/games.json each build
```

## Adding a game
Append to `data/games.json`, drop a static file in `play/`, then:
```bash
python scripts/build.py && python scripts/check-seo.py
```

## Slot Games (Blender-MCP frontend)

`slots/` hosts the Slot Games single-page app from the Blender-MCP module
(`slot_games_mcp/frontend`). It is a **static** SPA: the reels, paytable and all
bonus animations — cascades, sticky-wild respins, Hold & Win, cash collect and
the jackpot wheel — run entirely in the browser. The seven titles
(`golden_riches`, `lucky_diamond`, `temple_falls`, `phoenix_fortune`,
`deep_sea_jackpot`, `dragon_cash_hold_win`, `pirate_treasure_collect`) are driven
by the Python engine that exposes `/api/games`, `/api/paytable`, `/api/spin` and
`/api/rtp`.

> **Backend note.** This site is fully static, so it ships **no** Python runtime.
> `SPIN` calls `/api/*` on the same origin, which 404s here. To make the reels
> actually spin, host the engine separately — e.g. `python -m slot_games.http.api`
> on any host exposing the four `/api/*` routes — and open the page with
> `?api=https://your-engine-host`, e.g.
> `https://user.github.io/<repo>/slots/?api=https://slots.example.com`. The
> frontend prepends that origin to every API call; without it the page still
> renders the full UI and paytable as a read-only showcase.

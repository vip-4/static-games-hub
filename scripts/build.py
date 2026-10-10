#!/usr/bin/env python3
"""
Build the deployable static site into dist/.

Why this exists (SEO): the game catalog used to be rendered client-side by
JavaScript, so crawlers that do not execute JS could see an empty list. This
script pre-renders data/games.json into real <li> elements at build time, so
index.html / games.html ship complete, indexable HTML. JavaScript then only
adds filtering (progressive enhancement).

Usage:
    python scripts/build.py                                  # root domain -> dist/
    python scripts/build.py --base /repo/ --site-url URL     # GitHub Pages subpath
    python scripts/build.py --site-url https://openarc.com --cname openarc.com

--base rewrites internal absolute links ("/assets/...") so the same site also
works when hosted under a sub-path such as https://user.github.io/repo/.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA = os.path.join(ROOT, "data", "games.json")
DIST = os.path.join(ROOT, "dist")
PAGES = ["index.html", "games.html", "404.html"]
COPY = ["assets", "data", "slots", "robots.txt", "_headers", "_redirects"]

DEFAULT_SITE = "https://openarc.example.com"

# Default thumbnail glyph used when a catalog entry omits "icon". Kept as a plain
# string constant so it never lands inside an f-string expression (which would
# raise SyntaxError on Python < 3.12, e.g. the CI runner's Python 3.11).
DEFAULT_ICON = "\U0001F3AE"


def load_games() -> list[dict]:
    with open(DATA, encoding="utf-8") as fh:
        return json.load(fh)


def play_href(g: dict) -> str:
    """Canonical link for a catalog entry: its own indexable detail page.

    Local playable games are embedded inside that page (and linked to /play/),
    external games link out from it — so every game gets one crawlable URL.
    """
    return f"/games/{g['id']}.html"


def render_item(g: dict) -> str:
    e = html.escape
    ea = lambda s: html.escape(s, quote=True)  # noqa: E731 (attr-safe escaping)
    cover = g.get("cover")
    if cover:
        # Decorative cover; the emoji glyph is the accessible fallback if the
        # image fails to load (onerror removes the <img>, revealing the glyph).
        thumb = (f'<img class="cover" src="{ea(cover)}" alt="" loading="lazy" '
                 f'width="400" height="120" onerror="this.remove()">')
    else:
        thumb = ""
    tags = "".join(f'<span class="tag">{e(t)}</span>' for t in g.get("tags", []))
    lic = f'<span class="tag lic">{e(g["license"])}</span>' if g.get("license") else ""
    ext = "" if (g.get("playable") and g.get("local")) else ' target="_blank" rel="noopener noreferrer"'
    label = "Play" if g.get("playable") else "View source"
    return (
        '        <li class="card"'
        f' data-source="{e(g.get("source", "other"))}"'
        f' data-tags="{e(" ".join(g.get("tags", [])))}">\n'
        f'          <div class="thumb" aria-hidden="true">{thumb}{e(g.get("icon", DEFAULT_ICON))}</div>\n'
        f'          <h3>{e(g["title"])}</h3>\n'
        f'          <p>{e(g.get("description", ""))}</p>\n'
        '          <div class="meta">\n'
        f'            <span class="tag src">{e(g.get("source", ""))}</span>{lic}{tags}\n'
        '          </div>\n'
        f'          <a class="btn play" href="{e(play_href(g))}"{ext}>{label}</a>\n'
        '        </li>'
    )


def render_itemlist(games: list[dict], site_url: str) -> str:
    site = site_url.rstrip("/")
    items = []
    for i, g in enumerate(games, start=1):
        items.append({
            "@type": "ListItem",
            "position": i,
            "name": g.get("title", ""),
            "url": site + play_href(g),
        })
    payload = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "OpenArc open-source static games",
        "numberOfItems": len(games),
        "itemListElement": items,
    }
    body = json.dumps(payload, ensure_ascii=False)
    return f'  <script type="application/ld+json">\n{body}\n  </script>'


def render_game_content(g: dict, site_url: str) -> str:
    """Unique, indexable body copy for a game page + HowTo/Breadcrumb schema.

    Content lives in data/games.json (howto / controls / tips) so every page gets
    real, non-thin text and structured data without hand-editing HTML.
    """
    e = html.escape
    site = site_url.rstrip("/")
    url = site + f"/play/{g['id']}.html"
    title = e(g["title"])
    out = []

    if g.get("howto"):
        steps = "\n".join(f"          <li>{e(s)}</li>" for s in g["howto"])
        out.append(
            '      <section aria-labelledby="how-h">\n'
            f'        <h2 id="how-h">How to play {title}</h2>\n'
            f'        <ol class="steps">\n{steps}\n        </ol>\n'
            '      </section>'
        )
    if g.get("controls"):
        out.append(
            '      <section aria-labelledby="ctl-h">\n'
            '        <h2 id="ctl-h">Controls</h2>\n'
            f'        <p>{e(g["controls"])}</p>\n'
            '      </section>'
        )
    if g.get("tips"):
        items = "\n".join(f"          <li>{e(t)}</li>" for t in g["tips"])
        out.append(
            '      <section aria-labelledby="tips-h">\n'
            '        <h2 id="tips-h">Tips</h2>\n'
            f'        <ul>\n{items}\n        </ul>\n'
            '      </section>'
        )

    if g.get("howto"):
        howto = {
            "@context": "https://schema.org",
            "@type": "HowTo",
            "name": f"How to play {g['title']}",
            "description": g.get("description", ""),
            "step": [{"@type": "HowToStep", "position": i, "text": s}
                     for i, s in enumerate(g["howto"], start=1)],
        }
        out.append('      <script type="application/ld+json">\n'
                   + json.dumps(howto, ensure_ascii=False) + "\n      </script>")

    crumbs = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": site + "/"},
            {"@type": "ListItem", "position": 2, "name": "Games", "item": site + "/games.html"},
            {"@type": "ListItem", "position": 3, "name": g["title"], "item": url},
        ],
    }
    out.append('      <script type="application/ld+json">\n'
               + json.dumps(crumbs, ensure_ascii=False) + "\n      </script>")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Per-game DETAIL pages (one indexable page per catalog entry; GEO + sitemap)
# --------------------------------------------------------------------------- #
GAME_TMPL = os.path.join(ROOT, "game.html")

GENRE_MAP = {
    "arcade": "Arcade", "puzzle": "Puzzle", "strategy": "Strategy",
    "action": "Action", "shooter": "Shooter", "rpg": "Role-Playing",
    "simulation": "Simulation", "sandbox": "Sandbox", "io": ".io",
    "casino": "Casino", "memory": "Puzzle", "mmorpg": "MMORPG",
    "multiplayer": "Multiplayer",
}

# Human-readable source names, used to keep per-game page titles unique
# (e.g. "Minesweeper" appears both as an OpenArc game and a Simple-Game-Tutorials one).
SRC_NAME = {
    "openarc": "OpenArc",
    "simplegametutorials": "Simple Game Tutorials",
    "itchio": "itch.io",
    "ludumdare": "Ludum Dare",
    "github": "GitHub",
}

LICENSE_URL = {
    "MIT": "https://opensource.org/licenses/MIT",
    "Apache-2.0": "https://www.apache.org/licenses/LICENSE-2.0",
    "GPL-3.0": "https://www.gnu.org/licenses/gpl-3.0.html",
    "GPL-2.0": "https://www.gnu.org/licenses/old-licenses/gpl-2.0.html",
    "BSD-3-Clause": "https://opensource.org/licenses/BSD-3-Clause",
    "BSD-2-Clause": "https://opensource.org/licenses/BSD-2-Clause",
    "MPL-2.0": "https://www.mozilla.org/MPL/2.0/",
    "AGPL-3.0": "https://www.gnu.org/licenses/agpl-3.0.html",
    "LGPL-3.0": "https://www.gnu.org/licenses/lgpl-3.0.html",
    "Unlicense": "https://unlicense.org/",
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
}


def _genre(g: dict) -> str:
    for t in g.get("tags", []):
        if t in GENRE_MAP:
            return GENRE_MAP[t]
    return "Indie"


def _license_url(g: dict) -> str | None:
    lic = g.get("license", "")
    return LICENSE_URL.get(lic)


def game_schema_head(g: dict, site_url: str, image: str | None = None) -> str:
    """Build head JSON-LD: VideoGame + BreadcrumbList as separate <script> blocks."""
    e = html.escape
    site = site_url.rstrip("/")
    gid = g["id"]
    url = f"{site}/games/{gid}.html"
    title = g["title"]
    desc = g.get("description", "")

    vg: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "VideoGame",
        "name": title,
        "description": desc,
        "url": url,
        "genre": _genre(g),
        "playMode": "SinglePlayer",
        "gamePlatform": "Web Browser",
        "applicationCategory": "Game",
        "operatingSystem": "Any (browser)",
        "isAccessibleForFree": True,
    }
    if image:
        vg["image"] = image
    lic_url = _license_url(g)
    if lic_url:
        vg["license"] = lic_url

    crumbs = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": site + "/"},
            {"@type": "ListItem", "position": 2, "name": "Games", "item": site + "/games.html"},
            {"@type": "ListItem", "position": 3, "name": title, "item": url},
        ],
    }
    faq_html, faq_json = game_faq(g, site, url)
    blocks = [
        '<script type="application/ld+json">',
        json.dumps(vg, ensure_ascii=False),
        "</script>",
        '<script type="application/ld+json">',
        json.dumps(crumbs, ensure_ascii=False),
        "</script>",
        '<script type="application/ld+json">',
        json.dumps(faq_json, ensure_ascii=False),
        "</script>",
    ]
    return "\n".join(blocks)


def game_faq(g: dict, site: str, url: str) -> tuple[str, dict]:
    """Per-game FAQ block + FAQPage JSON-LD for generative-engine citation."""
    e = html.escape
    title = g["title"]
    tags = g.get("tags", [])
    lic = g.get("license", "varies")
    controls = g.get("controls") or (
        "Open the game in your browser and use the on-screen controls; "
        "everything runs client-side with no install."
    )
    qas = [
        (f"What is {title}?",
         f"{title} is a lightweight, open-source "
         f"{', '.join(tags) if tags else 'HTML5'} game in the OpenArc catalog. "
         f"It runs in any modern browser with plain HTML, CSS and JavaScript."),
        (f"Is {title} open source?",
         f"Yes — it is released under the {lic} license and links to its source, "
         f"so you can read, modify and redistribute it."),
        (f"How do I play {title}?",
         controls),
        (f"Can I self-host {title}?",
         "Yes. OpenArc is a fully static site: copy the built dist/ folder to any "
         "static host (GitHub Pages, Netlify, Cloudflare Pages) and the game stays "
         "available offline."),
    ]
    parts = ['      <section aria-labelledby="gfaq-h">',
             f'        <h2 id="gfaq-h">{e(title)} — FAQ</h2>',
             '        <div class="faq">']
    faq_json: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [],
    }
    for q, a in qas:
        parts.append(f'          <details>\n            <summary>{e(q)}</summary>\n'
                     f'            <p>{e(a)}</p>\n          </details>')
        faq_json["mainEntity"].append({
            "@type": "Question", "name": q,
            "acceptedAnswer": {"@type": "Answer", "text": a},
        })
    parts.append("        </div>\n      </section>")
    return "\n".join(parts), faq_json


def render_game_detail(g: dict, site_url: str) -> str:
    """Render a full, indexable detail page for one catalog entry."""
    e = html.escape
    ea = lambda s: html.escape(s, quote=True)  # noqa: E731 (short helper)
    site = site_url.rstrip("/")
    gid = g["id"]
    url = f"{site}/games/{gid}.html"
    title = g["title"]
    desc = g.get("description", "")
    tags = g.get("tags", [])
    lic = g.get("license", "varies")
    cover = g.get("cover")
    # Social/crawler preview image: the game cover if we have one, else the
    # site default. Cover URLs are absolute (Pexels/Pixabay CDN), so use as-is.
    og_img = cover if cover else (site + "/assets/img/cover.png")

    keywords = ", ".join([title] + tags + [g.get("source", "other"), lic,
                                          "open source game", "HTML5 game",
                                          "static game", "browser game"])

    # Per-game page title must be unique across the catalog (same game name can
    # appear under different sources), so disambiguate by source.
    src = g.get("source", "other")
    src_name = SRC_NAME.get(src, src)
    page_title = f"{title} | OpenArc" if src == "openarc" else f"{title} · {src_name} | OpenArc"

    # ---- visible body ----
    body = []
    if cover:
        body.append(
            '      <figure class="game-cover-wrap">\n'
            f'        <img class="game-cover" src="{ea(cover)}" '
            f'alt="{e(title)} — themed artwork" loading="lazy" '
            f'width="1200" height="630">\n      </figure>')
    body.append("      <dl class=\"game-meta\">")
    body.append(f"        <dt>Source</dt><dd>{e(src_name)}</dd>")
    body.append(f"        <dt>License</dt><dd>{e(lic)}</dd>")
    body.append(f"        <dt>Tags</dt><dd>{e(', '.join(tags) if tags else '—')}</dd>")
    if g.get("stars"):
        body.append(f"        <dt>Stars</dt><dd>{e(str(g['stars']))}</dd>")
    body.append("      </dl>")

    if g.get("playable") and g.get("local"):
        body.append(
            f'      <div class="stage">\n'
            f'        <iframe src="/play/{e(gid)}.html" title="Play {e(title)}" '
            f'loading="lazy" class="game-frame"></iframe>\n'
            f'        <p><a class="btn primary" href="/play/{e(gid)}.html">'
            f'Play {e(title)} full screen</a></p>\n      </div>')
    else:
        ext = ea(g.get("url", "#"))
        body.append(
            f'      <p><a class="btn primary" href="{ext}" '
            f'target="_blank" rel="noopener noreferrer">'
            f'Play / View source on {e(g.get("source", "upstream"))}</a></p>')

    if g.get("controls"):
        body.append(
            '      <section aria-labelledby="ctl-h">\n'
            '        <h2 id="ctl-h">Controls</h2>\n'
            f'        <p>{e(g["controls"])}</p>\n      </section>')
    if g.get("howto"):
        steps = "\n".join(f"          <li>{e(s)}</li>" for s in g["howto"])
        body.append(
            '      <section aria-labelledby="how-h">\n'
            f'        <h2 id="how-h">How to play {e(title)}</h2>\n'
            f'        <ol class="steps">\n{steps}\n        </ol>\n      </section>')
    if g.get("tips"):
        items = "\n".join(f"          <li>{e(t)}</li>" for t in g["tips"])
        body.append(
            '      <section aria-labelledby="tips-h">\n'
            '        <h2 id="tips-h">Tips</h2>\n'
            f'        <ul>\n{items}\n        </ul>\n      </section>')

    # per-game FAQ (also carries FAQPage JSON-LD)
    faq_html, _ = game_faq(g, site, url)
    body.append(faq_html)

    # ---- template substitution ----
    with open(GAME_TMPL, encoding="utf-8") as fh:
        tpl = fh.read()
    repl = {
        "__TITLE__": ea(page_title),
        "__H1__": e(title),
        "__DESC__": ea(desc),
        "__KEYWORDS__": ea(keywords),
        "__CANON__": ea(url),
        "__OG_TITLE__": ea(page_title),
        "__OG_DESC__": ea(desc),
        "__TW_TITLE__": ea(page_title),
        "__TW_DESC__": ea(desc),
        "__OG_IMG__": ea(og_img),
        "__LEAD__": ea(desc or title),
        "<!--JSONLD-->": game_schema_head(g, site_url, image=og_img),
        "<!--GAME_CONTENT-->": "\n".join(body),
    }
    for k, v in repl.items():
        tpl = tpl.replace(k, v)
    return tpl


SOURCE_NOTES = {
    "openarc": "Self-hosted here — MIT licensed, dependency-free, playable without leaving the site.",
    "simplegametutorials": "MIT-licensed vanilla-JavaScript classics.",
    "itchio": "Indie games published under open-source licenses.",
    "ludumdare": "Game-jam entries, many released with their source.",
    "github": "Trending open-source HTML5 / Canvas / WebGL repositories.",
}


def render_source_section(games: list[dict]) -> str:
    """Per-source breakdown: adds semantic headings and indexable copy."""
    e = html.escape
    groups: dict[str, list[dict]] = {}
    for g in games:
        groups.setdefault(g.get("source", "other"), []).append(g)
    rows = []
    for src, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        note = SOURCE_NOTES.get(src, "Community open-source listings.")
        lic = ", ".join(sorted({i.get("license", "varies") for i in items}))
        rows.append(
            f"        <dt>{e(src)} — {len(items)} "
            f"{'game' if len(items) == 1 else 'games'} ({e(lic)})</dt>\n"
            f"        <dd>{e(note)}</dd>"
        )
    return (
        '    <section aria-labelledby="by-src-h">\n'
        '      <h2 id="by-src-h">Catalog by source</h2>\n'
        f'      <dl>\n' + "\n".join(rows) + '\n      </dl>\n'
        '    </section>'
    )


# Conservative HTML minifier: collapses indentation/blank lines and drops HTML
# comments, but NEVER touches <script>, <style>, <pre> or <textarea> contents,
# and never removes a single meaningful space between inline elements.
_PROTECT = re.compile(
    r"(<script\b.*?</script>|<style\b.*?</style>|<pre\b.*?</pre>|<textarea\b.*?</textarea>)",
    re.S | re.I,
)


def minify_html(text: str) -> str:
    chunks = _PROTECT.split(text)
    for i, chunk in enumerate(chunks):
        if i % 2 == 1:  # protected block — leave untouched
            continue
        chunk = re.sub(r"<!--(?!\[if).*?-->", "", chunk, flags=re.S)
        chunk = re.sub(r">[ \t]*\n[ \t]*<", "><", chunk)
        chunk = re.sub(r"\n\s*\n+", "\n", chunk)
        chunk = re.sub(r"[ \t]{2,}", " ", chunk)
        chunks[i] = chunk
    return "".join(chunks)


def minify_tree() -> tuple[int, int, int]:
    """Minify every built .html file. Returns (files, bytes_before, bytes_after)."""
    before = after = n = 0
    for root, _dirs, files in os.walk(DIST):
        for name in files:
            if not name.endswith(".html"):
                continue
            p = os.path.join(root, name)
            with open(p, encoding="utf-8") as fh:
                src = fh.read()
            out = minify_html(src)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(out)
            before += len(src.encode("utf-8"))
            after += len(out.encode("utf-8"))
            n += 1
    return n, before, after


def build_play_pages(games: list[dict], site_url: str) -> int:
    """Copy play/*.html, injecting catalog-driven content + schema.

    Each play page's canonical URL is pointed at its detail page so search
    engines index one URL per game (the detail page) instead of duplicating.
    """
    src_dir = os.path.join(ROOT, "play")
    dst_dir = os.path.join(DIST, "play")
    os.makedirs(dst_dir, exist_ok=True)
    by_id = {g["id"]: g for g in games}
    site = site_url.rstrip("/")
    n = 0
    for name in sorted(os.listdir(src_dir)):
        if not name.endswith(".html"):
            continue
        with open(os.path.join(src_dir, name), encoding="utf-8") as fh:
            content = fh.read()
        gid = name[:-5]
        g = by_id.get(gid)
        injected = render_game_content(g, site_url) if g else ""
        content = content.replace("<!--GAME_CONTENT-->", injected)
        # consolidate canonical -> /games/{id}.html
        content = re.sub(
            r'(<link rel="canonical" href=")[^"]*(")',
            rf'\1{site}/games/{gid}.html\2', content)
        with open(os.path.join(dst_dir, name), "w", encoding="utf-8") as fh:
            fh.write(content)
        n += 1
    return n


def build_game_pages(games: list[dict], site_url: str) -> int:
    """Generate one indexable detail page per catalog entry under dist/games/."""
    dst_dir = os.path.join(DIST, "games")
    os.makedirs(dst_dir, exist_ok=True)
    n = 0
    for g in games:
        gid = g["id"]
        # safe filename: keep only [A-Za-z0-9._-]
        safe = re.sub(r"[^A-Za-z0-9._-]", "-", gid)
        out = render_game_detail(g, site_url)
        with open(os.path.join(dst_dir, f"{safe}.html"), "w", encoding="utf-8") as fh:
            fh.write(out)
        n += 1
    return n


def render_sitemap(games: list[dict], today: str, site_url: str) -> str:
    """Generate sitemap.xml from the catalog so URLs can never drift.

    Every catalog entry gets its own canonical detail page, plus the two hubs.
    Games that have a `cover` image also emit an <image:image> entry (Google
    image-sitemap extension) so the artwork is discoverable.
    (The /play/*.html shells are reachable from inside those pages but are
    consolidated via rel=canonical, so they are intentionally omitted here.)
    """
    base = site_url.rstrip("/")
    urls = [
        (f"{base}/", "daily", "1.0", None),
        (f"{base}/games.html", "daily", "0.9", None),
    ]
    for g in games:
        urls.append((f"{base}/games/{g['id']}.html", "monthly", "0.7", g.get("cover")))
    body = "\n".join(
        "  <url>\n"
        f"    <loc>{loc}</loc>\n"
        f"    <lastmod>{today}</lastmod>\n"
        f"    <changefreq>{freq}</changefreq>\n"
        f"    <priority>{prio}</priority>\n"
        + (f"    <image:image>\n      <image:loc>{img}</image:loc>\n    </image:image>\n"
           if img else "")
        + "  </url>"
        for loc, freq, prio, img in urls
    )
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">\n'
            f"{body}\n</urlset>\n")


def render_llms(games: list[dict], today: str) -> str:
    """Generate llms.txt — a curated plain-text summary for AI crawlers (GEO).

    Emerging convention (llmstxt.org): a compact, high-signal document that
    generative engines can ingest instead of parsing heavy HTML.
    """
    hosted = sum(1 for g in games if g.get("local"))
    linked = len(games) - hosted
    lines = [
        "# OpenArc",
        "",
        "> OpenArc is a free, fully static hub of lightweight open-source HTML5 games.",
        "> Every title runs on plain HTML, CSS and JavaScript — no plugins, no downloads,",
        "> no backend, no trackers. You can play instantly or self-host the whole catalog.",
        "",
        f"Last updated: {today}",
        "",
        "## Key facts",
        "- What it is: a static catalog of open-source HTML5 browser games.",
        f"- Catalog size: {len(games)} entries — {hosted} playable and self-hosted here, {linked} linked to upstream source.",
        "- Licenses: MIT and other permissive licenses; re-use is permitted.",
        "- Sources: itch.io, GitHub, Ludum Dare, Simple Game Tutorials.",
        "- Self-hosting: one folder, no database, no API keys required.",
        "- Tracking: none; games run client-side in the browser.",
        "",
        "## Games",
    ]
    for g in games:
        target = play_href(g)
        lines.append(f"- **{g['title']}** ({g.get('source', 'other')}, {g.get('license', 'varies')}) "
                     f"— {g.get('description', '')} → {target}")
    lines += [
        "",
        "## FAQ (frequently cited answers)",
        "",
        "Q: What is OpenArc?",
        "A: A free static catalog of lightweight open-source HTML5 games you can play in a browser or self-host.",
        "",
        "Q: Are the games open source?",
        "A: Yes. Every entry carries a license (most are MIT) and links to its source repository.",
        "",
        "Q: Do I need to install anything?",
        "A: No. Games run in the browser with plain HTML, CSS and JavaScript; no plugins, downloads or accounts.",
        "",
        "Q: Does OpenArc track visitors?",
        "A: No. Zero analytics, cookies and third-party trackers; games execute locally.",
        "",
        "Q: How do I self-host it?",
        "A: Run scripts/build.py and upload dist/ to any static host (Netlify, Cloudflare Pages, GitHub Pages, R2, S3).",
        "",
        "## Machine-readable catalog",
        "- /data/games.json — full game metadata as JSON.",
        "- /sitemap.xml — indexable URLs.",
        "",
    ]
    return "\n".join(lines)


def rewrite_paths(text: str, base: str, site_url: str) -> str:
    """Point canonical/OG URLs at the real domain and prefix internal links."""
    text = text.replace(DEFAULT_SITE, site_url.rstrip("/"))
    if base != "/":
        b = base if base.endswith("/") else base + "/"
        text = text.replace('href="/', f'href="{b}')
        text = text.replace('src="/', f'src="{b}')
        text = text.replace('"/data/games.json"', f'"{b}data/games.json"')
        text = text.replace("'/data/games.json'", f"'{b}data/games.json'")
    return text


def rewrite_tree(base: str, site_url: str) -> int:
    """Apply path rewriting to every built HTML/JS file."""
    n = 0
    for root, _dirs, files in os.walk(DIST):
        for name in files:
            # .txt included so robots.txt picks up the real sitemap origin
            if not name.endswith((".html", ".js", ".txt")):
                continue
            p = os.path.join(root, name)
            with open(p, encoding="utf-8") as fh:
                out = rewrite_paths(fh.read(), base, site_url)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(out)
            n += 1
    return n


def build(base: str = "/", site_url: str = DEFAULT_SITE, cname: str | None = None,
          minify: bool = True) -> None:
    games = load_games()
    items_html = "\n".join(render_item(g) for g in games)
    itemlist = render_itemlist(games, site_url)
    today = date.today().isoformat()

    if os.path.isdir(DIST):
        shutil.rmtree(DIST)
    os.makedirs(DIST, exist_ok=True)

    for page in PAGES:
        src = os.path.join(ROOT, page)
        with open(src, encoding="utf-8") as fh:
            content = fh.read()
        content = content.replace("<!--STATIC_GAMES-->", items_html)
        content = content.replace("<!--ITEMLIST_JSONLD-->", itemlist)
        content = content.replace("<!--SOURCE_SECTION-->", render_source_section(games))
        with open(os.path.join(DIST, page), "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"built {page} ({len(games)} games)")

    for entry in COPY:
        s, d = os.path.join(ROOT, entry), os.path.join(DIST, entry)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        elif os.path.isfile(s):
            shutil.copy2(s, d)
        print(f"copied {entry}")

    # play pages get unique copy + HowTo/Breadcrumb schema injected
    n_play = build_play_pages(games, site_url)
    print(f"built {n_play} play pages (content + schema injected)")

    # per-game detail pages: canonical URL + VideoGame/Breadcrumb/FAQ schema
    n_detail = build_game_pages(games, site_url)
    print(f"built {n_detail} game detail pages (independent meta + sitemap entries)")

    # sitemap generated from the catalog (no manual drift)
    sm = os.path.join(DIST, "sitemap.xml")
    with open(sm, "w", encoding="utf-8") as fh:
        fh.write(render_sitemap(games, today, site_url))
    print(f"generated sitemap.xml (lastmod {today})")

    # GEO artifact for AI crawlers
    llms = os.path.join(DIST, "llms.txt")
    with open(llms, "w", encoding="utf-8") as fh:
        fh.write(render_llms(games, today))
    print(f"generated llms.txt ({os.path.getsize(llms)} bytes)")

    if base != "/":
        print(f"rewrote internal links for base path '{base}' ({rewrite_tree(base, site_url)} files)")
    else:
        rewrite_tree(base, site_url)

    if minify:
        n, before, after = minify_tree()
        if before:
            print(f"minified {n} html files: {before / 1024:.1f} KB -> "
                  f"{after / 1024:.1f} KB (-{100 * (1 - after / before):.0f}%)")

    # GitHub Pages: skip Jekyll processing (it would strip paths / ignore files)
    with open(os.path.join(DIST, ".nojekyll"), "w", encoding="utf-8") as fh:
        fh.write("")
    if cname:
        with open(os.path.join(DIST, "CNAME"), "w", encoding="utf-8") as fh:
            fh.write(cname + "\n")
        print(f"wrote CNAME -> {cname}")

    print(f"\ndist/ ready: {len(games)} games, site={site_url} base={base}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the static site into dist/.")
    ap.add_argument("--base", default="/",
                    help="URL prefix for internal links, e.g. /repo/ for GitHub Pages project sites")
    ap.add_argument("--site-url", default=DEFAULT_SITE,
                    help="public origin used for canonical / OG / sitemap URLs")
    ap.add_argument("--cname", default=None, help="write a CNAME file with this custom domain")
    ap.add_argument("--no-minify", action="store_true",
                    help="skip HTML minification (useful when debugging output)")
    a = ap.parse_args()
    build(a.base, a.site_url, a.cname, minify=not a.no_minify)


if __name__ == "__main__":
    main()

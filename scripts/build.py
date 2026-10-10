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


def load_games() -> list[dict]:
    with open(DATA, encoding="utf-8") as fh:
        return json.load(fh)


def play_href(g: dict) -> str:
    if g.get("playable") and g.get("local"):
        return f"/play/{g['id']}.html"
    return g.get("url") or "#"


def render_item(g: dict) -> str:
    e = html.escape
    tags = "".join(f'<span class="tag">{e(t)}</span>' for t in g.get("tags", []))
    lic = f'<span class="tag lic">{e(g["license"])}</span>' if g.get("license") else ""
    ext = "" if (g.get("playable") and g.get("local")) else ' target="_blank" rel="noopener noreferrer"'
    label = "Play" if g.get("playable") else "View source"
    return (
        '        <li class="card"'
        f' data-source="{e(g.get("source", "other"))}"'
        f' data-tags="{e(" ".join(g.get("tags", [])))}">\n'
        f'          <div class="thumb" aria-hidden="true">{e(g.get("icon", "\U0001F3AE"))}</div>\n'
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
    """Copy play/*.html, injecting catalog-driven content + schema."""
    src_dir = os.path.join(ROOT, "play")
    dst_dir = os.path.join(DIST, "play")
    os.makedirs(dst_dir, exist_ok=True)
    by_id = {g["id"]: g for g in games}
    n = 0
    for name in sorted(os.listdir(src_dir)):
        if not name.endswith(".html"):
            continue
        with open(os.path.join(src_dir, name), encoding="utf-8") as fh:
            content = fh.read()
        g = by_id.get(name[:-5])
        injected = render_game_content(g, site_url) if g else ""
        content = content.replace("<!--GAME_CONTENT-->", injected)
        with open(os.path.join(dst_dir, name), "w", encoding="utf-8") as fh:
            fh.write(content)
        n += 1
    return n


def render_sitemap(games: list[dict], today: str, site_url: str) -> str:
    """Generate sitemap.xml from the catalog so URLs can never drift."""
    base = site_url.rstrip("/")
    urls = [
        (f"{base}/", "daily", "1.0"),
        (f"{base}/games.html", "daily", "0.9"),
    ]
    for g in games:
        if g.get("playable") and g.get("local"):
            urls.append((f"{base}/play/{g['id']}.html", "monthly", "0.6"))
    body = "\n".join(
        "  <url>\n"
        f"    <loc>{loc}</loc>\n"
        f"    <lastmod>{today}</lastmod>\n"
        f"    <changefreq>{freq}</changefreq>\n"
        f"    <priority>{prio}</priority>\n"
        "  </url>"
        for loc, freq, prio in urls
    )
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
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

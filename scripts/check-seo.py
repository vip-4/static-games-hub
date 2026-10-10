#!/usr/bin/env python3
"""
Validate the built site in dist/ for SEO / GEO / accessibility basics.

Checks:
  * required meta tags (title, description, keywords, canonical, robots)
  * Open Graph + Twitter Card completeness
  * JSON-LD blocks parse as JSON and use known schema types
  * the game list is PRE-RENDERED (crawlable without JavaScript)
  * semantic landmarks (main, h1) and a skip link exist
  * sitemap / robots / OG image present

Usage:
    python scripts/build.py && python scripts/check-seo.py
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIST = os.path.join(ROOT, "dist")
PAGES = [
    "index.html",
    "games.html",
    "play/snake.html",
    "play/tic-tac-toe.html",
    "play/2048.html",
    "play/memory.html",
    "play/breakout.html",
    "play/pong.html",
    "play/minesweeper.html",
]

REQUIRED_META = [
    ("title", r"<title>.+</title>"),
    ("description", r'<meta\s+name="description"'),
    ("keywords", r'<meta\s+name="keywords"'),
    ("canonical", r'rel="canonical"'),
    ("robots", r'<meta\s+name="robots"'),
    ("viewport", r'<meta\s+name="viewport"'),
    ("og:title", r'property="og:title"'),
    ("og:description", r'property="og:description"'),
    ("og:image", r'property="og:image"'),
    ("og:url", r'property="og:url"'),
    ("twitter:card", r'name="twitter:card"'),
    ("twitter:title", r'name="twitter:title"'),
    ("twitter:image", r'name="twitter:image"'),
]

failures: list[str] = []
warnings: list[str] = []


def check(cond: bool, ok: str, bad: str, warn_only: bool = False) -> None:
    if cond:
        print(f"  ok   {ok}")
    elif warn_only:
        warnings.append(bad)
        print(f"  WARN {bad}")
    else:
        failures.append(bad)
        print(f"  FAIL {bad}")


def main() -> int:
    if not os.path.isdir(DIST):
        print("dist/ not found — run scripts/build.py first")
        return 1

    for page in PAGES:
        path = os.path.join(DIST, page)
        print(f"\n[{page}]")
        if not os.path.isfile(path):
            failures.append(f"missing page {page}")
            print(f"  FAIL missing {page}")
            continue
        html = open(path, encoding="utf-8").read()

        for name, pat in REQUIRED_META:
            check(re.search(pat, html, re.I) is not None,
                  f"meta {name}", f"missing meta {name}",
                  warn_only=name == "keywords")

        check("<main" in html, "has <main> landmark", "missing <main>")
        check(re.search(r"<h1[\s>]", html) is not None, "has <h1>", "missing <h1>")
        check("skip-link" in html, "has skip link", "missing skip link")

        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
        check(bool(blocks), "has JSON-LD", "no JSON-LD")
        for b in blocks:
            try:
                data = json.loads(b)
            except json.JSONDecodeError as exc:
                failures.append(f"{page}: invalid JSON-LD ({exc})")
                print(f"  FAIL invalid JSON-LD: {exc}")
                continue
            t = data.get("@type", "?")
            print(f"  ok   JSON-LD @type={t}")
            if t not in {"Organization", "WebSite", "BreadcrumbList", "ItemList",
                         "FAQPage", "HowTo", "VideoGame"}:
                warnings.append(f"{page}: unknown schema type {t}")

    # Pre-rendered catalog (the key crawler-indexability check)
    print("\n[crawlability]")
    idx = open(os.path.join(DIST, "index.html"), encoding="utf-8").read()
    n = idx.count('<li class="card"')
    check(n > 0, f"index.html pre-renders {n} game items",
          "games are NOT pre-rendered (JS-only) — crawlers may see an empty list")
    check("<!--STATIC_GAMES-->" not in idx, "build placeholder consumed",
          "STATIC_GAMES placeholder still present — build did not run")
    for t in ("Snake", "Tic-Tac-Toe", "2048"):
        check(t in idx, f"static HTML contains '{t}'", f"'{t}' missing from static HTML")
    check("noscript" in idx, "has <noscript> fallback", "no noscript fallback", warn_only=True)

    # ---- thin-content guard: pages must carry real, indexable copy ----
    print("\n[content depth — thin-content guard]")

    def body_words(page_html: str) -> int:
        main = re.search(r"<main\b.*?</main>", page_html, re.S)
        chunk = main.group(0) if main else page_html
        text = re.sub(r"<script.*?</script>", " ", chunk, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        return len(text.split())

    for page in PAGES:
        path = os.path.join(DIST, page)
        if not os.path.isfile(path):
            continue
        words = body_words(open(path, encoding="utf-8").read())
        floor = 120 if page in ("index.html", "games.html") else 60
        check(words >= floor, f"{page} body has {words} words (>= {floor})",
              f"{page} is thin: only {words} words (min {floor})")

    idx_again = open(os.path.join(DIST, "index.html"), encoding="utf-8").read()
    check("How to play" in open(os.path.join(DIST, "play/snake.html"), encoding="utf-8").read(),
          "play pages carry injected how-to copy", "play pages missing injected content")
    check(idx_again.count("<h2") >= 4, "homepage has multiple h2 sections",
          "homepage has too few h2 sections")

    # ---- sitemap + llms coverage ----
    print("\n[sitemap / llms.txt]")
    sm_path = os.path.join(DIST, "sitemap.xml")
    sm_txt = open(sm_path, encoding="utf-8").read()
    for page in PAGES:
        if page == "404.html":
            continue
        # the homepage is listed as "/" rather than "/index.html"
        path = "/" if page == "index.html" else f"/{page}"
        check(path in sm_txt, f"sitemap lists {path}", f"sitemap missing {path}")
    ll_path = os.path.join(DIST, "llms.txt")
    check(os.path.isfile(ll_path) and os.path.getsize(ll_path) > 800,
          "llms.txt present and substantive", "llms.txt missing or too short")

    # ---- duplicate meta: two pages sharing a title/description compete in SERPs ----
    print("\n[meta uniqueness]")
    titles: dict[str, list[str]] = {}
    descs: dict[str, list[str]] = {}
    for page in PAGES:
        path = os.path.join(DIST, page)
        if not os.path.isfile(path):
            continue
        h = open(path, encoding="utf-8").read()
        t = re.search(r"<title>(.*?)</title>", h, re.S)
        d = re.search(r'<meta\s+name="description"\s+content="(.*?)"', h, re.S)
        if t:
            titles.setdefault(t.group(1).strip(), []).append(page)
        if d:
            descs.setdefault(d.group(1).strip(), []).append(page)
    dup_t = {k: v for k, v in titles.items() if len(v) > 1}
    dup_d = {k: v for k, v in descs.items() if len(v) > 1}
    check(not dup_t, f"{len(titles)} unique page titles", f"duplicate titles: {dup_t}")
    check(not dup_d, f"{len(descs)} unique descriptions", f"duplicate descriptions: {dup_d}")

    # ---- heading order: skipped levels break screen-reader navigation ----
    print("\n[heading order]")
    for page in PAGES:
        path = os.path.join(DIST, page)
        if not os.path.isfile(path):
            continue
        h = open(path, encoding="utf-8").read()
        levels = [int(n) for n in re.findall(r"<h([1-6])\b", h)]
        skips = [(levels[i], levels[i + 1]) for i in range(len(levels) - 1)
                 if levels[i + 1] - levels[i] > 1]
        check(not skips, f"{page} heading order ok ({len(levels)} headings)",
              f"{page} skips heading levels: {skips}")

    # ---- internal link integrity (catches 404s before deploy) ----
    print("\n[internal links]")
    broken = []
    for root, _dirs, files in os.walk(DIST):
        for name in files:
            if not name.endswith(".html"):
                continue
            p = os.path.join(root, name)
            h = open(p, encoding="utf-8").read()
            for href in re.findall(r'href="([^"]+)"', h):
                if href.startswith(("http", "#", "mailto:", "tel:", "data:", "//")):
                    continue
                clean = href.split("#")[0].split("?")[0]
                if not clean:
                    continue
                # strip any base-path prefix (e.g. /repo/assets/... -> assets/...)
                rel = clean.lstrip("/")
                target = os.path.join(DIST, rel)
                if not os.path.exists(target):  # retry one level up (base-path build)
                    target = os.path.join(DIST, "/".join(rel.split("/")[1:]))
                if not os.path.exists(target):
                    broken.append((os.path.relpath(p, DIST), href))
    check(not broken, "all internal links resolve",
          f"broken internal links: {broken[:10]}")

    print("\n[assets]")
    for f in ("sitemap.xml", "robots.txt", "assets/img/cover.png", "data/games.json"):
        check(os.path.isfile(os.path.join(DIST, f)), f, f"missing {f}")

    og_img = re.search(r'property="og:image"\s+content="([^"]+)"',
                       open(os.path.join(DIST, "index.html"), encoding="utf-8").read())
    if og_img:
        rel = og_img.group(1).split("/assets/")[-1]
        check(os.path.isfile(os.path.join(DIST, "assets", rel)),
              "og:image resolves to a real file", f"og:image missing: {rel}")

    print("\n" + "-" * 46)
    print(f"failures: {len(failures)}  warnings: {len(warnings)}")
    for f in failures:
        print(f"  FAIL {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

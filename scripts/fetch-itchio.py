#!/usr/bin/env python3
"""Scrape real open-source games from itch.io and merge them into the catalog.

This is the itch.io counterpart to ``scripts/fetch-games.py``; it is built the
same way so the two can be chained in the daily refresh workflow. It is fully
self-contained (no cross-module imports) so it runs reliably from any cwd.

Why scraping (not the API):
    itch.io has *no* official "search the whole site by open-source tag" API — the
    game API (``/api/1/<key>/search/games``) and ``my-games`` are account-scoped,
    so they only return games owned by a specific key. The public, always-fresh
    source for "open-source games on itch.io" is the tag browse page:
        https://itch.io/games/tag-<tag>
    We scrape that (with pagination), keeping only live, real games.

Entries are merged by id/url and NEVER overwrite existing catalog entries, so the
script is safe to re-run on a schedule.

Usage:
    python scripts/fetch-itchio.py --tag open-source --pages 3 --merge
    python scripts/fetch-itchio.py --tag open-source --no-merge --out data/games.itchio.json
    python scripts/fetch-itchio.py --tag open-source --merge --pixabay-key $PKEY   # enrich covers
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import urllib.parse
import urllib.request as u
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0"}
DEFAULT_TAG = "open-source"

# Optional best-effort genre hint keyed by the slug (last URL path segment).
# Keeps categorization accurate without claiming a license we can't verify.
ITCH_GENRE = {
    "mindustry": "strategy", "funkin": "rhythm", "battle-for-wesnoth": "strategy",
    "supertuxkart": "racing", "pocket-bird": "casual", "openra": "strategy",
    "clan-gen-fan-edit": "simulation", "catburglar": "arcade", "retroarch": "emulator",
    "nanosaur": "action", "0ad": "strategy", "dr-virtual-pet": "casual",
    "open-saber": "action", "cromagrally": "racing", "unimaker": "sandbox",
}


# --------------------------------------------------------------------------
# Reused helpers (mirrors scripts/fetch-games.py)
# --------------------------------------------------------------------------
def merge_into(games: list[dict], new: list[dict]) -> int:
    """Append only NEW entries (by id or url); never overwrite existing ones."""
    have_id = {g.get("id") for g in games}
    have_url = {g.get("url") for g in games}
    added = 0
    for it in new:
        if it.get("id") in have_id or it.get("url") in have_url:
            continue
        games.append(it)
        have_id.add(it.get("id"))
        have_url.add(it.get("url"))
        added += 1
    return added


def _cover_from_pixabay(key: str, query: str, timeout: int = 20):
    try:
        q = urllib.parse.quote(query)
        url = f"https://pixabay.com/api/?key={key}&q={q}&image_type=photo&per_page=3&safesearch=true"
        req = u.Request(url, headers=UA)
        with u.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        for hit in data.get("hits", []):
            for cand in (hit.get("largeImageURL"), hit.get("webformatURL"), hit.get("previewURL")):
                if cand:
                    return cand
    except Exception:
        pass
    return None


def _cover_from_pexels(key: str, query: str, timeout: int = 20):
    try:
        q = urllib.parse.quote(query)
        url = f"https://api.pexels.com/v1/search?query={q}&per_page=1"
        req = u.Request(url, headers={"Authorization": key, "User-Agent": "Mozilla/5.0"})
        with u.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        ph = data.get("photos", [])
        if ph:
            return ph[0]["src"].get("large") or ph[0]["src"].get("medium")
    except Exception:
        pass
    return None


def enrich_covers(games: list[dict], pixabay_key, pexels_key) -> None:
    if not (pixabay_key or pexels_key):
        return
    for g in games:
        if g.get("cover"):
            continue
        query = g.get("title", "") or " ".join(g.get("tags", []))
        cov = (_cover_from_pixabay(pixabay_key, query) if pixabay_key else None) \
            or (_cover_from_pexels(pexels_key, query) if pexels_key else None)
        if cov:
            g["cover"] = cov


# --------------------------------------------------------------------------
# Scraper
# --------------------------------------------------------------------------
def _slug(url: str) -> str:
    seg = url.rstrip("/").split("/")[-1]
    return re.sub(r"[^a-z0-9]+", "-", seg.lower()).strip("-")


def _get(url: str) -> str:
    import time
    last = None
    for attempt in range(3):
        try:
            req = u.Request(url, headers={**UA, "Accept": "text/html,application/xhtml+xml"})
            with u.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # transient block/rate-limit — back off and retry
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


def fetch_tag(tag: str, pages: int, limit: int) -> list[dict]:
    out: list[dict] = []
    seen_urls = set()
    for p in range(1, pages + 1):
        url = f"https://itch.io/games/tag-{urllib.parse.quote(tag)}"
        if p > 1:
            url += f"?page={p}"
        try:
            html = _get(url)
        except Exception as e:  # network/blocked — keep what we have
            print(f"WARN could not fetch itch.io tag page {p}: {e}", file=sys.stderr)
            break
        for cell in re.split(r'class="game_cell', html)[1:]:
            if len(out) >= limit:
                break
            t = re.search(r'class="title game_link" href="([^"]+)">([^<]+)<', cell)
            if not t:
                continue
            game_url = t.group(1)
            if game_url in seen_urls:
                continue
            seen_urls.add(game_url)
            slug = _slug(game_url)
            author = re.search(r'class="game_author"><a[^>]*href="([^"]+)">([^<]+)<', cell)
            cover = re.search(r'data-lazy_src="([^"]+)"', cell)
            title = t.group(2).strip()
            author_name = author.group(2).strip() if author else ""
            genre = ITCH_GENRE.get(slug)
            tags = ["open-source", "itchio"] + ([genre] if genre else [])
            desc = (f"{title} is an open-source game by {author_name}, "
                    f"published on itch.io" + (f" — a {genre} game." if genre else "."))
            out.append({
                "id": "itch-" + slug,
                "title": title,
                "description": desc,
                "icon": "🎮",
                "source": "itchio",
                "license": "open-source",
                "tags": tags,
                "playable": True,
                "local": False,
                "url": game_url,
                "author": author_name,
                "cover": cover.group(1) if cover else None,
            })
        if len(out) >= limit:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape itch.io open-source games into the catalog.")
    ap.add_argument("--tag", default=DEFAULT_TAG, help="itch.io tag to scrape (default: open-source).")
    ap.add_argument("--pages", type=int, default=3, help="Number of browse pages to fetch.")
    ap.add_argument("--limit", type=int, default=50, help="Max entries to keep from this run.")
    ap.add_argument("--out", default=str(ROOT / "data" / "games.json"),
                    help="Catalog file to read/merge into (or write if --no-merge).")
    ap.add_argument("--merge", action="store_true",
                    help="Merge into --out (dedupe by id/url, never overwrite). "
                         "Without it, write the generated list to --out instead.")
    ap.add_argument("--no-merge", dest="merge", action="store_false")
    ap.add_argument("--pixabay-key", default=None, help="Optional Pixabay key to enrich covers.")
    ap.add_argument("--pexels-key", default=None, help="Optional Pexels key to enrich covers.")
    ap.add_argument("--dry-run", action="store_true", help="Scrape but do not write anything.")
    ap.set_defaults(merge=False)
    args = ap.parse_args()

    generated = fetch_tag(args.tag, args.pages, args.limit)
    if args.pixabay_key or args.pexels_key:
        enrich_covers(generated, args.pixabay_key, args.pexels_key)

    gen_path = ROOT / "data" / "games.itchio.generated.json"
    if args.dry_run:
        print(f"[dry-run] scraped {len(generated)} itch.io '{args.tag}' entries; nothing written.")
        return 0

    gen_path.write_text(json.dumps(generated, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.merge:
        games = json.loads(Path(args.out).read_text(encoding="utf-8"))
        added = merge_into(games, generated)
        Path(args.out).write_text(json.dumps(games, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"INFO itch.io: scraped {len(generated)} entries, merged {added} NEW into {args.out}.")
    else:
        Path(args.out).write_text(json.dumps(generated, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"INFO itch.io: wrote {len(generated)} entries -> {args.out} (no merge).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

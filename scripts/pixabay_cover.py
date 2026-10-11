#!/usr/bin/env python3
"""Replace Ludum Dare entry covers with real Pixabay images.

Runs in CI with the ``PIXABAY_KEY`` secret (see .github/workflows/refresh-catalog.yml).
It overrides the cover of every ``ludumdare`` catalog entry with a themed,
safe-search Pixabay photo so each jam entry gets a real visual. It is a no-op
(and exits 0) when ``PIXABAY_KEY`` is not set, so local/CI runs without the
secret stay safe.

Usage:
    PIXABAY_KEY=xxxxx python scripts/pixabay_cover.py
"""
from __future__ import annotations
import json
import os
import sys
import urllib.parse
import urllib.request as u
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY = os.environ.get("PIXABAY_KEY")


def _cover(query: str, timeout: int = 25):
    try:
        q = urllib.parse.quote(query)
        url = (f"https://pixabay.com/api/?key={KEY}&q={q}&image_type=photo"
               f"&per_page=3&safesearch=true&orientation=horizontal")
        req = u.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with u.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        for hit in data.get("hits", []):
            for cand in (hit.get("largeImageURL"), hit.get("webformatURL"), hit.get("previewURL")):
                if cand:
                    return cand
    except Exception as e:
        print(f"WARN pixabay query '{query}': {e}", file=sys.stderr)
    return None


def main() -> int:
    if not KEY:
        print("SKIP pixabay_cover: PIXABAY_KEY not set.")
        return 0
    games = json.loads((ROOT / "data" / "games.json").read_text(encoding="utf-8"))
    skip = {"open-source", "ludumdare", "ld25", "ld26", "ld31", "ld47", "ld48", "ld49",
            "arcade", "action", "puzzle", "strategy", "rpg", "space", "roguelike",
            "exploration", "adventure", "escape", "platformer", "management",
            "tower-defense", "survival", "minimalist"}
    n = 0
    for g in games:
        if g.get("source") != "ludumdare" or str(g.get("id", "")).startswith("browse-"):
            continue
        theme = next((t for t in g.get("tags", []) if t not in skip), None)
        q = g["title"] + (f" {theme}" if theme else "")
        cov = _cover(q)
        if cov:
            g["cover"] = cov
            n += 1
    (ROOT / "data" / "games.json").write_text(
        json.dumps(games, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"pixabay: updated covers for {n} Ludum Dare entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Refresh the OpenArc catalog from public open-source game sources.

SECURITY: This script reads API tokens ONLY from environment variables.
No credentials are stored in the repository. The secrets you may hold for
itch.io / GitHub are never written to disk by this script.

Usage:
    export ITCH_IO_API_KEY="..."      # optional, for itch.io (public browse used if absent)
    python scripts/fetch-games.py

Output: data/games.generated.json  (merge into data/games.json as you see fit)
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

GITHUB_API = "https://api.github.com/search/repositories"
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "games.generated.json")


def _get_json(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "openarc-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 (https only)
        return json.loads(r.read().decode())


def fetch_github(topic: str = "html5-game", limit: int = 12) -> list[dict]:
    """GitHub trending-style search (public, no auth required)."""
    q = urllib.parse.quote(f"topic:{topic} game stars:>5")
    url = f"{GITHUB_API}?q={q}&sort=stars&order=desc&per_page={limit}"
    data = _get_json(url)
    out = []
    for it in data.get("items", []):
        out.append({
            "id": "gh-" + it["name"].lower().replace(" ", "-"),
            "title": it["name"],
            "description": (it.get("description") or "")[:160],
            "icon": "\U0001F5A5",
            "source": "github",
            "license": (it.get("license") or {}).get("spdx_id") or "varies",
            "tags": ["open-source", "static"],
            "playable": False,
            "local": False,
            "url": it["html_url"],
        })
    return out


def fetch_itchio_browse() -> list[dict]:
    """itch.io has no open public catalog API without OAuth.

    When ITCH_IO_API_KEY is set it is read from the environment only; we still
    fall back to the public tag page so the script works without secrets.
    """
    _ = os.environ.get("ITCH_IO_API_KEY")  # read from env, never printed/stored
    return [{
        "id": "browse-itchio-live",
        "title": "itch.io · open-source (live)",
        "description": "Live listing of open-source indie games on itch.io.",
        "icon": "\U0001F3AE",
        "source": "itchio",
        "license": "varies",
        "tags": ["open-source", "indie"],
        "playable": False,
        "local": False,
        "url": "https://itch.io/games/tag-open-source",
    }]


def main() -> None:
    games = []
    try:
        games += fetch_github()
    except Exception as exc:  # network/offline is non-fatal
        print(f"[warn] github fetch failed: {exc}")
    games += fetch_itchio_browse()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(games, fh, indent=2, ensure_ascii=False)
    print(f"wrote {len(games)} entries -> {os.path.normpath(OUT)}")


if __name__ == "__main__":
    main()

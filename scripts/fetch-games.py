#!/usr/bin/env python3
"""
Refresh the OpenArc catalog from public open-source game sources.

SECURITY
--------
This script reads API tokens ONLY from environment variables (or CLI flags that
are passed on the command line, never written to disk). No credentials are stored
in the repository. The itch.io / GitHub secrets you hold are never printed or
persisted by this script.

What it does
------------
1. itch.io (authenticated, when ITCH_IO_API_KEY is set)
   - Validates the key format AND liveness before use.
   - Pulls the authenticated account's published games via the official REST API
     endpoint  GET https://itch.io/api/1/<KEY>/my-games
   - IMPORTANT: the itch.io API is account-scoped — it returns games published by
     the key owner, it cannot search the whole site by the "open-source" tag. The
     public open-source browse page is therefore always kept as a fallback link.
   - Rate limits (HTTP 429) are handled with exponential back-off + retries.
2. GitHub (public search API + a curated allow-list of verified repos)
   - Searches several game-oriented topics, sorted by stars, de-duplicated.
   - Verifies every curated repo live via the REST API (skipped if it 404s, so a
     stale entry can never fabricate a broken link).
   - Optional GITHUB_TOKEN raises the rate limit (read-only use).

Outputs
-------
  data/games.generated.json          # always written (the raw fetched catalog)
  data/games.json                    # only with --merge: new ids are appended
                                     # (existing entries are NEVER overwritten)

Usage
-----
  # dry run — just see what would be fetched
  python scripts/fetch-games.py

  # use a real itch.io key + github token (env vars, never stored)
  set ITCH_IO_API_KEY=xxxx
  set GITHUB_TOKEN=ghp_xxx
  python scripts/fetch-games.py --merge

  # fully configurable
  python scripts/fetch-games.py \
      --itch-key "$ITCH_IO_API_KEY" \
      --github-token "$GITHUB_TOKEN" \
      --topics html5-game,canvas-game,webgl-game,browser-game \
      --curated mozilla/BrowserQuest,ellisonleao/clumsy-bird \
      --min-stars 20 --limit 8 --out data/games.generated.json --merge

Verification
------------
  * Key validity is printed in plain language (valid / invalid / rate-limited / missing).
  * Every network error is logged at WARNING/ERROR and is non-fatal (the script
    still emits a usable catalog).
  * `--merge` reports exactly how many NEW entries were appended to games.json.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA = os.path.join(ROOT, "data")
OUT_DEFAULT = os.path.join(DATA, "games.generated.json")
GAMES_JSON = os.path.join(DATA, "games.json")

ITCH_API = "https://itch.io/api/1"
GITHUB_API = "https://api.github.com"
GITHUB_SEARCH = f"{GITHUB_API}/search/repositories"

log = logging.getLogger("openarc-fetch")

# A repo is treated as "not a game" when its name OR description matches these.
NON_GAME_WORDS = (
    "library", "framework", "tutorial", "boilerplate", "starter", "template",
    "course", "sdk", "engine", "awesome", "example", "demo", "binding",
    "patterns", "challenge", "emulator", "docs", "showcase", "sample",
    "port", "curated list", "cheat", "book", "boiler",
)

# Curated, verified open-source HTML5 / JS browser games. Each is re-checked
# live via the GitHub API at fetch time, so a moved/renamed repo is skipped
# gracefully instead of producing a dead link.
DEFAULT_CURATED = [
    "FreezingMoon/AncientBeast",   # already in catalog (de-duped) — kept for safety
    "Kaetram/Kaetram-Open",
    "R74nCom/sandboxels",
    "kenrick95/c4",
    "knagaitsev/slither.io-clone",
    "mozilla/BrowserQuest",        # classic Mozilla HTML5 MMORPG
    "ellisonleao/clumsy-bird",     # popular Flappy-Bird-style clone (MIT)
]

DEFAULT_TOPICS = [
    "html5-game", "canvas-game", "webgl-game",
    "browser-game", "game-off", "js13k", "ludum-dare",
]

BROWSE_ITCHIO = {
    "id": "browse-itchio",
    "title": "itch.io · open-source tag",
    "description": "Browse indie games published with open-source licenses on itch.io.",
    "icon": "\U0001F3AE",
    "source": "itchio",
    "license": "varies",
    "tags": ["open-source", "indie"],
    "playable": False,
    "local": False,
    "url": "https://itch.io/games/tag-open-source",
}


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _get_json(url: str, headers: Optional[dict] = None, timeout: int = 25):
    """Return (status, parsed_json) or (status, raw_text) on non-JSON bodies."""
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "openarc-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (https only)
        body = r.read().decode("utf-8", "replace")
        try:
            return r.status, json.loads(body)
        except json.JSONDecodeError:
            return r.status, body


def _itch_call(key: str, path: str):
    return _get_json(f"{ITCH_API}/{key}/{path}",
                     headers={"User-Agent": "openarc-fetch/1.0"})


def _gh_call(url: str, token: Optional[str]):
    headers = {"User-Agent": "openarc-fetch/1.0",
               "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return _get_json(url, headers=headers)


# --------------------------------------------------------------------------- #
# itch.io
# --------------------------------------------------------------------------- #
def validate_itch_key(key: Optional[str]) -> dict:
    """Return a verdict dict: status + human-readable reason + fix advice."""
    if not key:
        return {
            "status": "missing",
            "reason": "ITCH_IO_API_KEY is empty or unset.",
            "fix": "Generate a key at https://itch.io/user/settings/api-keys and pass it "
                   "via the ITCH_IO_API_KEY environment variable (or --itch-key).",
        }
    problems = []
    if len(key) != 40:
        problems.append(f"length is {len(key)} characters (itch.io keys are 40)")
    if not key.isalnum():
        problems.append("contains characters outside [A-Za-z0-9] "
                        "(often a copied space or newline from the dashboard)")

    # Authoritative check: a live probe to the authenticated /me endpoint.
    try:
        status, body = _itch_call(key, "me")
    except urllib.error.HTTPError as e:  # noqa: BLE001
        status, body = e.code, ""
    except Exception as e:  # noqa: BLE001  (network / DNS failure)
        return {
            "status": "unknown",
            "reason": f"could not reach itch.io to verify the key ({e}).",
            "fix": "Check connectivity and retry; the key format looks OK"
                   + (" (" + "; ".join(problems) + ")" if problems else "") + ".",
        }

    who = ""
    if status == 200 and isinstance(body, dict):
        who = body.get("user", {}).get("username", "")

    if status == 200:
        note = f"authenticated as itch.io user '{who}'."
        if problems:
            note += " (Note: " + "; ".join(problems) + " — but the live probe succeeded, "
            note += "so the key still works; re-copy it cleanly to silence the warning.)"
        return {"status": "valid", "reason": note, "fix": ""}
    if status in (401, 403):
        return {
            "status": "invalid",
            "reason": f"itch.io rejected the key (HTTP {status}).",
            "fix": "The key is wrong, expired or revoked. Regenerate it at "
                   "https://itch.io/user/settings/api-keys, copy it without any extra "
                   "spaces or newlines, and ensure it is the full 40-character value.",
        }
    if status == 429:
        return {
            "status": "ratelimited",
            "reason": "key format is OK but the API is rate-limiting requests (HTTP 429).",
            "fix": "Retry shortly — this script already backs off and retries automatically.",
        }
    return {
        "status": "error",
        "reason": f"unexpected HTTP {status} from itch.io.",
        "fix": "Retry; if it persists the key or the service may be unhealthy.",
    }


def _map_itch_game(g: dict) -> dict:
    desc = (g.get("short_text") or "").strip() or g.get("title") or "itch.io game"
    return {
        "id": "itch-" + str(g.get("id")),
        "title": g.get("title", "itch game"),
        "description": desc[:200],
        "icon": "\U0001F3AE",
        "source": "itchio",
        "license": "varies",
        "tags": ["indie", "itchio"],
        "playable": False,
        "local": False,
        "url": g.get("url", ""),
    }


def fetch_itchio(key: Optional[str], include_browse: bool = True) -> list[dict]:
    """Fetch the key-owner's published games; fall back to the browse link."""
    verdict = validate_itch_key(key)
    log.info("itch.io key: %s — %s", verdict["status"], verdict["reason"])
    if verdict["fix"]:
        log.info("  ↳ fix: %s", verdict["fix"])

    if verdict["status"] != "valid":
        return [BROWSE_ITCHIO] if include_browse else []

    games: list[dict] = []
    for attempt in range(1, 4):
        try:
            status, body = _itch_call(key, "my-games")
        except Exception as e:  # noqa: BLE001
            log.warning("itch.io my-games request failed (%s); retry %d/3", e, attempt)
            time.sleep(2 ** attempt)
            continue
        if status == 200 and isinstance(body, dict):
            for g in body.get("games", []):
                games.append(_map_itch_game(g))
            log.info("itch.io: fetched %d game(s) owned by the key.", len(games))
            break
        if status == 429:
            wait = 2 ** attempt
            log.warning("itch.io 429 — backing off %ss (attempt %d/3)", wait, attempt)
            time.sleep(wait)
            continue
        log.error("itch.io my-games returned HTTP %s; using browse link only.", status)
        break

    if include_browse:
        games.append(BROWSE_ITCHIO)
    return games


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #
def _map_repo(it: dict) -> dict:
    gid = "gh-" + re.sub(r"[^a-z0-9]+", "-", it["name"].lower()).strip("-")
    return {
        "id": gid,
        "title": it["name"],
        "description": (it.get("description") or "")[:180],
        "icon": "\U0001F916",
        "source": "github",
        "license": (it.get("license") or {}).get("spdx_id") or "varies",
        "tags": ["open-source", "html5"],
        "playable": False,
        "local": False,
        "url": it["html_url"],
        "stars": it.get("stargazers_count", 0),
    }


def _is_game(it: dict) -> bool:
    hay = f"{it.get('name', '')} {it.get('description') or ''}".lower()
    return not any(w in hay for w in NON_GAME_WORDS)


def fetch_github(topics: list[str], curated: list[str], token: Optional[str],
                 limit: int, min_stars: int, cap: int) -> list[dict]:
    out: dict[str, dict] = {}
    order: list[str] = []

    def add(it: dict) -> bool:
        if not _is_game(it):
            return False
        gid = "gh-" + re.sub(r"[^a-z0-9]+", "-", it["name"].lower()).strip("-")
        if gid in out:
            return False
        out[gid] = _map_repo(it)
        order.append(gid)
        return True

    # 1) curated allow-list FIRST — guaranteed, verified-live quality repos
    for full in curated:
        if len(out) >= cap:
            break
        try:
            status, body = _gh_call(f"{GITHUB_API}/repos/{full}", token)
        except Exception as e:  # noqa: BLE001
            log.warning("github repo %s check failed: %s", full, e)
            continue
        if status == 200 and isinstance(body, dict):
            if add(body):
                log.info("github curated '%s' included.", full)
        else:
            log.warning("github curated '%s' not found (HTTP %s) — skipped.", full, status)
        time.sleep(0.6)

    # 2) topic searches (trending-style, sorted by stars) fill the rest up to cap
    for t in topics:
        if len(out) >= cap:
            break
        q = urllib.parse.quote(f"topic:{t} stars:>{min_stars} fork:false")
        url = f"{GITHUB_SEARCH}?q={q}&sort=stars&order=desc&per_page={limit}"
        try:
            status, body = _gh_call(url, token)
        except Exception as e:  # noqa: BLE001
            log.warning("github search '%s' failed: %s", t, e)
            continue
        if status != 200:
            log.warning("github search '%s' HTTP %s (rate-limited?); skipping topic.", t, status)
            if status in (403, 429):
                time.sleep(2)
            continue
        for it in body.get("items", []):
            add(it)
            if len(out) >= cap:
                break
        log.info("github topic '%s': %d games so far (cap %d)", t, len(out), cap)
        time.sleep(1.2)  # stay polite with the search rate limit

    return [out[k] for k in order]


# --------------------------------------------------------------------------- #
# Merge + persistence
# --------------------------------------------------------------------------- #
def load_json(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def merge_into(generated: list[dict], path: str) -> int:
    existing = load_json(path) if os.path.exists(path) else []
    # De-duplicate by id OR by source URL so a repo fetched under a slightly
    # different generated id (e.g. gh-kaetram-open vs an existing gh-kaetram)
    # never creates a duplicate catalog entry.
    have_id = {g.get("id") for g in existing}
    have_url = {g.get("url") for g in existing}
    added = 0
    for g in generated:
        gid = g.get("id")
        url = g.get("url")
        if (gid and gid in have_id) or (url and url in have_url):
            continue
        existing.append(g)
        if gid:
            have_id.add(gid)
        if url:
            have_url.add(url)
        added += 1
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(existing, fh, indent=2, ensure_ascii=False)
    return added


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Refresh the OpenArc open-source game catalog.")
    ap.add_argument("--itch-key",
                    default=os.environ.get("ITCH_IO_API_KEY"),
                    help="itch.io API key (defaults to $ITCH_IO_API_KEY).")
    ap.add_argument("--github-token",
                    default=os.environ.get("GITHUB_TOKEN"),
                    help="GitHub token for higher rate limits (defaults to $GITHUB_TOKEN).")
    ap.add_argument("--topics", default=",".join(DEFAULT_TOPICS),
                    help="Comma-separated GitHub topics to search.")
    ap.add_argument("--curated", default=",".join(DEFAULT_CURATED),
                    help="Comma-separated owner/repo allow-list (verified live).")
    ap.add_argument("--limit", type=int, default=6,
                    help="Max games per GitHub topic search.")
    ap.add_argument("--cap", type=int, default=18,
                    help="Hard cap on total GitHub entries fetched.")
    ap.add_argument("--min-stars", type=int, default=15,
                    help="Minimum stars for a GitHub repo to be included.")
    ap.add_argument("--out", default=OUT_DEFAULT,
                    help="Where to write the generated catalog JSON.")
    ap.add_argument("--no-itch", action="store_true", help="Skip itch.io entirely.")
    ap.add_argument("--no-github", action="store_true", help="Skip GitHub entirely.")
    ap.add_argument("--merge", action="store_true",
                    help="Append new ids into data/games.json (never overwrites).")
    ap.add_argument("--log-level", default="INFO",
                    help="DEBUG, INFO, WARNING, ERROR.")
    return ap.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(levelname)s %(message)s")

    generated: list[dict] = []
    if not args.no_github:
        topics = [t.strip() for t in args.topics.split(",") if t.strip()]
        curated = [c.strip() for c in args.curated.split(",") if c.strip()]
        log.info("GitHub: searching %d topics, %d curated repos (min %d stars).",
                 len(topics), len(curated), args.min_stars)
        generated += fetch_github(topics, curated, args.github_token,
                                 args.limit, args.min_stars, args.cap)
    if not args.no_itch:
        generated += fetch_itchio(args.itch_key)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(generated, fh, indent=2, ensure_ascii=False)
    log.info("wrote %d entries -> %s", len(generated), os.path.normpath(args.out))

    if args.merge:
        added = merge_into(generated, GAMES_JSON)
        log.info("merged %d NEW entries into %s (existing entries untouched).",
                 added, os.path.normpath(GAMES_JSON))
    else:
        log.info("dry run: pass --merge to append new ids into data/games.json.")

    by_src: dict[str, int] = {}
    for g in generated:
        by_src[g.get("source", "other")] = by_src.get(g.get("source", "other"), 0) + 1
    log.info("summary by source: %s", by_src)
    return 0


if __name__ == "__main__":
    sys.exit(main())

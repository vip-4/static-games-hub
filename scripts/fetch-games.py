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

Cover images (optional, --covers)
----------------------------------
  3. Pexels + Pixabay (when PEXELS_API_KEY / PIXABAY_API_KEY are set)
     - Enriches data/games.json with a `cover` image URL per game.
     - Pexels is tried first; Pixabay is used as a fallback.
     - ONLY the resulting image URL is written to disk — the API keys are read
       from the environment (or --pexels-key / --pixabay-key) and never stored.
     - `browse-*` link cards are skipped; existing covers are kept unless --force.

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

  # enrich the catalog with cover images (keys via env, never stored)
  set PEXELS_API_KEY=xxxx
  set PIXABAY_API_KEY=yyyy
  python scripts/fetch-games.py --covers
  python scripts/fetch-games.py --covers --force --cover-limit 10   # re-fetch 10

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
import urllib.parse
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

# Cover-image sources (optional — only used by --covers). Keys are read from
# environment variables / CLI flags, never written to disk.
PEXELS_API = "https://api.pexels.com/v1/search"
PIXABAY_API = "https://pixabay.com/api/"

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
# Cover images (Pexels / Pixabay) — optional, keyed by env var
# --------------------------------------------------------------------------- #
def validate_pexels_key(key: Optional[str]) -> dict:
    """Return a verdict dict: status + human-readable reason + fix advice."""
    if not key:
        return {"status": "missing",
                "reason": "PEXELS_API_KEY is empty or unset.",
                "fix": "Create a key at https://www.pexels.com/api/new and pass it via "
                       "the PEXELS_API_KEY environment variable (or --pexels-key)."}
    problems = []
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", key):
        problems.append("format looks unexpected (expected 20+ chars of [A-Za-z0-9_-])")
    try:
        status, _ = _get_json(f"{PEXELS_API}?query=test&per_page=1",
                              headers={"Authorization": key})
    except Exception as e:  # noqa: BLE001
        return {"status": "unknown",
                "reason": f"could not reach Pexels to verify the key ({e}).",
                "fix": "Check connectivity and retry; key format looks OK"
                       + (" (" + "; ".join(problems) + ")" if problems else "") + "."}
    if status == 200:
        note = "Pexels key authenticated."
        if problems:
            note += " (Note: " + "; ".join(problems) + " — but the live probe succeeded, "
            note += "so it still works; re-copy it cleanly to silence the warning.)"
        return {"status": "valid", "reason": note, "fix": ""}
    if status in (401, 403):
        return {"status": "invalid",
                "reason": f"Pexels rejected the key (HTTP {status}).",
                "fix": "The key is wrong, expired or revoked. Regenerate it at "
                       "https://www.pexels.com/api/new and copy it without extra spaces."}
    if status == 429:
        return {"status": "ratelimited",
                "reason": "Pexels key works but the API is rate-limiting (HTTP 429).",
                "fix": "Retry shortly — this script backs off automatically."}
    return {"status": "error", "reason": f"unexpected HTTP {status} from Pexels.",
            "fix": "Retry; if it persists the key or service may be unhealthy."}


def validate_pixabay_key(key: Optional[str]) -> dict:
    """Return a verdict dict: status + human-readable reason + fix advice."""
    if not key:
        return {"status": "missing",
                "reason": "PIXABAY_API_KEY is empty or unset.",
                "fix": "Create a key at https://pixabay.com/api/docs/ and pass it via "
                       "the PIXABAY_API_KEY environment variable (or --pixabay-key)."}
    problems = []
    if not re.fullmatch(r"\d+-[a-f0-9]+", key):
        problems.append("format looks unexpected (expected '<digits>-<hex>')")
    try:
        # Pixabay requires per_page >= 3; using 1 here would itself 400.
        status, _ = _get_json(f"{PIXABAY_API}?key={urllib.parse.quote(key)}&q=test&per_page=3")
    except Exception as e:  # noqa: BLE001
        return {"status": "unknown",
                "reason": f"could not reach Pixabay to verify the key ({e}).",
                "fix": "Check connectivity and retry; key format looks OK"
                       + (" (" + "; ".join(problems) + ")" if problems else "") + "."}
    if status == 200:
        note = "Pixabay key authenticated."
        if problems:
            note += " (Note: " + "; ".join(problems) + " — but the live probe succeeded.)"
        return {"status": "valid", "reason": note, "fix": ""}
    if status in (400, 401, 403):
        return {"status": "invalid",
                "reason": f"Pixabay rejected the key (HTTP {status}).",
                "fix": "The key is wrong or revoked. Regenerate it at "
                       "https://pixabay.com/api/docs/ and copy it without extra spaces."}
    return {"status": "error", "reason": f"unexpected HTTP {status} from Pixabay.",
            "fix": "Retry; if it persists the key or service may be unhealthy."}


def cover_queries(g: dict) -> list[str]:
    """Build an ordered list of search queries for a game's cover image.

    Most specific first (exact title), then a generic variant, then a
    genre-aware term so a topical image is used when no photo matches the
    exact title (common for niche open-source games).
    """
    _GENRE_TERM = {
        "arcade": "arcade game", "puzzle": "puzzle", "strategy": "strategy game",
        "action": "action game", "shooter": "space shooter", "rpg": "rpg",
        "simulation": "simulation", "sandbox": "sandbox", "io": "arcade game",
        "casino": "casino", "memory": "puzzle", "mmorpg": "fantasy rpg",
        "multiplayer": "multiplayer game",
    }
    title = (g.get("title") or "").strip()
    tags = g.get("tags", []) or []
    q: list[str] = []
    if title:
        q.append(title)
        q.append(f"{title} game")
    for t in tags:
        if t in _GENRE_TERM:
            q.append(_GENRE_TERM[t])
            break
    seen: set[str] = set()
    out: list[str] = []
    for x in q:
        if x and x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out or [title or "game"]


def fetch_cover_pexels(queries: list[str], key: str) -> Optional[str]:
    """Return a landscape cover URL from Pexels, or None on any failure.

    Tries each query in order; transient 403/429 are retried once per query.
    """
    for query in queries:
        url = (f"{PEXELS_API}?query={urllib.parse.quote(query)}"
               f"&per_page=3&orientation=landscape")
        for attempt in range(2):  # transient 403/429 can happen on shared IPs
            try:
                status, body = _get_json(url, headers={"Authorization": key})
            except Exception as e:  # noqa: BLE001
                log.warning("Pexels request failed for '%s': %s", query, e)
                return None
            if status == 200 and isinstance(body, dict):
                for photo in (body.get("photos") or []):
                    src = photo.get("src", {})
                    u = src.get("large") or src.get("medium") or src.get("original")
                    if u:
                        return u
                break  # no photo for this query; try the next query
            if status in (403, 429):
                log.warning("Pexels HTTP %s for '%s' — retry %d/2", status, query, attempt + 1)
                time.sleep(2 ** attempt)
                continue
            break
    return None


def fetch_cover_pixabay(queries: list[str], key: str) -> Optional[str]:
    """Return a cover URL from Pixabay (fallback source), or None on failure."""
    for query in queries:
        try:
            status, body = _get_json(
                f"{PIXABAY_API}?key={urllib.parse.quote(key)}&q={urllib.parse.quote(query)}"
                f"&image_type=photo&orientation=horizontal&per_page=3&safesearch=true")
        except Exception as e:  # noqa: BLE001
            log.warning("Pixabay request failed for '%s': %s", query, e)
            return None
        if status != 200 or not isinstance(body, dict):
            continue
        for hit in (body.get("hits") or []):
            url = hit.get("largeImageURL") or hit.get("webformatURL")
            if url:
                return url
    return None


def enrich_covers(path: str, pexels_key: Optional[str], pixabay_key: Optional[str],
                  force: bool = False, limit: Optional[int] = None) -> int:
    """Attach a `cover` URL to each catalog entry (Pexels first, Pixabay fallback).

    Only the resulting image URL is written back into games.json — API keys are
    never persisted. `browse-*` entries are skipped (they are link cards, not
    playable games). Existing covers are preserved unless `force` is set.
    """
    if not os.path.exists(path):
        log.error("catalog %s not found — run a fetch first.", path)
        return 0
    pv = validate_pexels_key(pexels_key)
    pxb = validate_pixabay_key(pixabay_key)
    log.info("Pexels key: %s — %s", pv["status"], pv["reason"])
    if pv["fix"]:
        log.info("  ↳ fix: %s", pv["fix"])
    log.info("Pixabay key: %s — %s", pxb["status"], pxb["reason"])
    if pxb["fix"]:
        log.info("  ↳ fix: %s", pxb["fix"])

    # We attempt a fetch whenever a key was *provided*, even if the pre-flight
    # probe was inconclusive (transient 403/429). The per-game fetch degrades
    # gracefully to None, so a bad/rate-limited key simply yields no cover.
    use_pexels = bool(pexels_key) and pv["status"] != "missing"
    use_pixabay = bool(pixabay_key) and pxb["status"] != "missing"
    if not use_pexels and not use_pixabay:
        log.warning("No cover-image key provided — leaving covers empty "
                    "(emoji fallback is used at build time).")
        return 0

    games = load_json(path)
    changed = done = 0
    for g in games:
        if limit is not None and done >= limit:
            break
        gid = g.get("id", "")
        if gid.startswith("browse-"):
            continue
        if g.get("cover") and not force:
            continue
        done += 1
        title = g.get("title", "")
        queries = cover_queries(g)
        cover = None
        if use_pexels:
            cover = fetch_cover_pexels(queries, pexels_key)
            if cover:
                log.info("cover (Pexels) for '%s'", title)
        if not cover and use_pixabay:
            cover = fetch_cover_pixabay(queries, pixabay_key)
            if cover:
                log.info("cover (Pixabay) for '%s'", title)
        if cover:
            g["cover"] = cover
            changed += 1
        else:
            log.warning("no cover found for '%s'", title)
        time.sleep(0.4)  # stay polite with the public APIs

    if changed:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(games, fh, indent=2, ensure_ascii=False)
        log.info("wrote %d cover URL(s) -> %s", changed, os.path.normpath(path))
    else:
        log.info("no covers added (all skipped or none found).")
    return changed


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
    ap.add_argument("--covers", action="store_true",
                    help="Enrich games.json with cover images from Pexels/Pixabay "
                         "(writes only the image URL, never the API key).")
    ap.add_argument("--pexels-key",
                    default=os.environ.get("PEXELS_API_KEY"),
                    help="Pexels API key (defaults to $PEXELS_API_KEY).")
    ap.add_argument("--pixabay-key",
                    default=os.environ.get("PIXABAY_API_KEY"),
                    help="Pixabay API key (defaults to $PIXABAY_API_KEY).")
    ap.add_argument("--force", action="store_true",
                    help="Re-fetch covers even if already set (with --covers).")
    ap.add_argument("--cover-limit", type=int, default=0,
                    help="Max games to enrich with --covers (0 = all).")
    ap.add_argument("--log-level", default="INFO",
                    help="DEBUG, INFO, WARNING, ERROR.")
    return ap.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(levelname)s %(message)s")

    # --covers is a standalone mode: enrich the existing catalog with images.
    if args.covers:
        added = enrich_covers(GAMES_JSON, args.pexels_key, args.pixabay_key,
                              args.force, args.cover_limit or None)
        log.info("covers added/updated: %d", added)
        return 0

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

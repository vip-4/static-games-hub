#!/usr/bin/env python3
"""
Deploy dist/ to a static host.

SECURITY CONTRACT
-----------------
* Credentials are read ONLY from environment variables (or CI secrets).
* This script NEVER accepts secrets as arguments and NEVER prints them.
* Nothing sensitive is written to disk.

Targets
-------
  r2    Cloudflare R2 (S3-compatible) — needs boto3 + R2_* env vars
  dry   List what would be uploaded (default; safe, no credentials needed)

Environment variables (names only, no values here):
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
  R2_ENDPOINT            (optional; derived from R2_ACCOUNT_ID if absent)

Usage:
  python scripts/deploy.py                 # dry run
  python scripts/deploy.py --target r2     # upload to R2 (requires env vars)
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIST = os.path.join(ROOT, "dist")

REQUIRED_ENV = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"]
LONG_CACHE = ("assets/", "data/")


def collect_files() -> list[tuple[str, str]]:
    """Return (absolute_path, relative_key) for every file in dist/."""
    out = []
    for base, _dirs, files in os.walk(DIST):
        for name in files:
            p = os.path.join(base, name)
            out.append((p, os.path.relpath(p, DIST).replace(os.sep, "/")))
    return sorted(out)


def content_type(key: str) -> str:
    ctype, _ = mimetypes.guess_type(key)
    return ctype or "application/octet-stream"


def cache_control(key: str) -> str:
    if key.startswith(LONG_CACHE):
        return "public, max-age=86400, stale-while-revalidate=604800"
    return "public, max-age=600, must-revalidate"


def dry_run(files: list[tuple[str, str]]) -> int:
    total = sum(os.path.getsize(p) for p, _ in files)
    print(f"DRY RUN — {len(files)} files, {total / 1024:.1f} KB from dist/")
    for _p, key in files:
        print(f"  would upload  {key}  ({content_type(key)})")
    print("\nNo credentials used. Upload with: --target r2")
    return 0


def deploy_r2(files: list[tuple[str, str]]) -> int:
    missing = [v for v in REQUIRED_ENV if not os.environ.get(v)]
    if missing:
        print("Missing environment variables (set them in your shell / CI secrets):")
        for v in missing:
            print(f"  - {v}")
        print("\nFalling back to dry run. No credentials were used.")
        return dry_run(files)

    try:
        import boto3  # noqa: PLC0415 (optional dependency)
    except ImportError:
        print("boto3 is required for R2 uploads:  pip install -r requirements-deploy.txt")
        return 2

    account = os.environ["R2_ACCOUNT_ID"]
    endpoint = os.environ.get("R2_ENDPOINT") or f"https://{account}.r2.cloudflarestorage.com"
    bucket = os.environ["R2_BUCKET"]

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )

    for path, key in files:
        s3.upload_fileobj(
            open(path, "rb"), bucket, key,
            ExtraArgs={"ContentType": content_type(key), "CacheControl": cache_control(key)},
        )
        print(f"  uploaded {key}")
    print(f"\nDone — {len(files)} files -> s3://{bucket}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy the built static site.")
    ap.add_argument("--target", choices=["dry", "r2"], default="dry")
    args = ap.parse_args()

    if not os.path.isdir(DIST):
        print("dist/ not found — run scripts/build.py first")
        return 1

    files = collect_files()
    if not files:
        print("dist/ is empty — run scripts/build.py first")
        return 1

    if args.target == "r2":
        return deploy_r2(files)
    return dry_run(files)


if __name__ == "__main__":
    sys.exit(main())

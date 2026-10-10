#!/usr/bin/env python3
"""Generate the Open Graph cover image (1200x630) using only the stdlib.

No Pillow / no external deps, so the build stays lightweight on any static host.
Writes assets/img/cover.png.
"""
from __future__ import annotations

import os
import struct
import zlib

W, H = 1200, 630
OUT = os.path.join(os.path.dirname(__file__), "..", "assets", "img", "cover.png")

TOP = (35, 40, 80)      # #232850
BOTTOM = (14, 16, 32)   # #0e1020
GOLD = (255, 210, 74)   # #ffd24a


def chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data +
            struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def write_png(path: str, rows: list[bytearray]) -> None:
    raw = b"".join(bytes(r) for r in rows)
    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)  # 8-bit RGB
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", ihdr))
        fh.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        fh.write(chunk(b"IEND", b""))


def main() -> None:
    rows = []
    for y in range(H):
        t = y / (H - 1)
        base = tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))
        # gold accent band across the middle third
        band = 300 <= y < 348
        row = bytearray([0])
        for _ in range(W):
            row.extend(GOLD if band else base)
        rows.append(row)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    write_png(OUT, rows)
    print(f"wrote {os.path.normpath(OUT)} ({W}x{H}, {os.path.getsize(OUT)} bytes)")


if __name__ == "__main__":
    main()

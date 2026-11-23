#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""List pictures in assets/products that are identical or nearly so (different products must have different pictures).

Uses a 16x16 difference hash: two pictures whose hashes differ in at most --threshold of 256 bits are reported.
Usage: python scripts/check_picture_duplicates.py [--threshold 24]   (exit code 1 if any pair is found)"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from itertools import combinations
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "assets" / "products"


def dhash(path: Path, size: int = 16) -> int:
    img = Image.open(path).convert("L").resize((size + 1, size))
    px = list(img.getdata())
    bits = 0
    for y in range(size):
        for x in range(size):
            bits = (bits << 1) | (px[y * (size + 1) + x] > px[y * (size + 1) + x + 1])
    return bits


def near_duplicates(files: dict[str, Path], threshold: int) -> list[tuple[str, str, int, bool]]:
    """[(id a, id b, differing bits, byte-identical)] for every pair at or under the threshold."""
    hashes = {pid: dhash(p) for pid, p in files.items()}
    digests = {pid: hashlib.sha256(p.read_bytes()).hexdigest() for pid, p in files.items()}  # a fingerprint to spot duplicates
    out = []
    for a, b in combinations(sorted(files), 2):
        d = bin(hashes[a] ^ hashes[b]).count("1")
        if d <= threshold or digests[a] == digests[b]:
            out.append((a, b, d, digests[a] == digests[b]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, default=24)
    args = ap.parse_args()
    manifest = json.loads((DIR / "manifest.json").read_text())
    files = {pid: DIR / name for pid, name in manifest.items() if (DIR / name).exists()}
    pairs = near_duplicates(files, args.threshold)
    titles = json.loads((ROOT / "assets" / "showcase-ids.json").read_text())
    for a, b, d, same in pairs:
        print(f"{'IDENTICAL' if same else 'similar  '} {d:3d} bits  {a} ({titles.get(a, '')[:34]})  ~  {b} ({titles.get(b, '')[:34]})")
    print(f"{len(files)} pictures, {len(pairs)} pair(s) at or under {args.threshold} bits")
    return 1 if pairs else 0


if __name__ == "__main__":
    sys.exit(main())

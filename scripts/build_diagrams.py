#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Draw the architecture pictures in docs/images from the Graphviz sources in docs/diagrams.

Needs the `dot` program (Graphviz, https://graphviz.org/download/). Writes one PNG and one SVG per source.

Usage: python scripts/build_diagrams.py [--out docs/images]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from retail_agent_demo import proc  # noqa: E402

SOURCES = ROOT / "docs" / "diagrams"


def sources() -> list[Path]:
    return sorted(SOURCES.glob("*.dot"))


def build(out: Path) -> list[Path]:
    dot = shutil.which("dot")
    if not dot:
        raise SystemExit("Graphviz is not installed: see https://graphviz.org/download/")
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for src in sources():
        for fmt, extra in (("png", ["-Gdpi=120"]), ("svg", [])):
            target = out / f"{src.stem}.{fmt}"
            proc.run(["dot", f"-T{fmt}", *extra, str(src), "-o", str(target)], check=True)
            written.append(target)
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "docs/images")
    args = ap.parse_args()
    for path in build(args.out):
        print("wrote", path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Take the screenshots used in the README and docs from a running app (needs the e2e extra and a deployed stack).

Usage: python scripts/capture_screenshots.py --url http://localhost:8501 [--out docs/images]

Writes guided-demo.png: the guided demo at the rerank step, with the result cards and the comparison table. Run it with the
demo shopper reset (scripts/sls_reset_demo_memory.py --user bob) so that nothing but the demo's own data is on screen.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from retail_agent_demo.waits import pause  # noqa: E402


def wait_for_text(page, text: str, seconds: int = 120) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if text in page.inner_text("body"):
            return
        pause(1)
    raise SystemExit(f"{text!r} did not appear within {seconds} s")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", type=Path, default=ROOT / "docs/images")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1250}, device_scale_factor=1)
        page.goto(args.url.rstrip("/") + "/?guided=1", wait_until="networkidle")
        wait_for_text(page, "Start the demo", 60)
        page.get_by_role("button", name="Start the demo").click()
        wait_for_text(page, "Lexical + vector (hybrid)")  # step 3 runs on start
        wait_for_text(page, "Comparison so far")
        page.get_by_role("button", name="Next ▶").click()
        wait_for_text(page, "Lexical + vector + rerank")
        pause(20)  # the rerank step takes a few seconds, and the table grows when it is done
        wait_for_text(page, "+ Rerank")
        page.screenshot(path=str(args.out / "guided-demo.png"))
        browser.close()
    print("wrote", args.out / "guided-demo.png")


if __name__ == "__main__":
    main()

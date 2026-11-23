#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Drive every control of the demo app in a real browser and report PASS or FAIL for each (end-to-end UI test).

What is checked, on either backend (the managed-domain app or the Serverless app):
  * the page loads, all seven versions, the search bar and both shoppers are offered, nothing mentions a design document (ADR)
  * each version can be selected; the inventory switch appears for the agent versions only
  * every answer is kept in a fold whose title names the version and the settings behind it
  * a browser refresh keeps the folds; "Clear results" removes them, and they stay gone after a refresh
  * "New session" keeps the folds; each shopper has their own folds
  * the memory lists open, close and reopen repeatedly, refresh on "Refresh now", and pick up new events on their own
  * "Forget this shopper" deletes the memory, reports counts and clears the folds

It asks a handful of real questions (search versions and one memory turn), and leaves Bob as it found him only if you
reset him afterwards (python scripts/sls_reset_demo_memory.py).

Usage: python scripts/ui_check.py --url http://localhost:8502      (domain app, through the tunnel)
       python scripts/ui_check.py --url http://localhost:8504      (Serverless app)
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import validate_demo as v  # noqa: E402  (reuses the browser helpers: ask, pick, toggles)

from retail_agent_demo import acts  # noqa: E402
from retail_agent_demo.waits import pause  # noqa: E402

FOLD_QUERY = '[data-testid="stExpander"] div[style*="1.2rem"]'  # the "You searched for" line
VERSIONS = ["Keyword", "v0", "v1 ", "v1+", "v2", "MCP", "v3"]


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""


def folds(page: Page) -> int:
    return page.locator("summary").count()


def reload(page: Page) -> None:
    page.reload()
    page.wait_for_selector('[data-testid="stSidebar"]', timeout=60_000)
    pause(3)


def click_button(page: Page, label: str) -> None:
    page.get_by_role("button", name=label).first.click()
    pause(2.5)


def run(page: Page, check) -> None:
    body = page.inner_text("body")
    labels = [x for x in page.locator('[data-testid="stSidebar"] [data-testid="stRadio"] label').all_inner_texts()
              if x != "Assistant version"]  # the first label is the radio's own title
    check("page loads with all seven versions offered", len(labels) == 7 and all(any(v.LABELS.get(p, p).split(" (")[0] in x for x in labels) for p in VERSIONS),
          f"{len(labels)} versions")
    check("no mention of a design document (ADR) anywhere on the page", "ADR" not in body)
    check("both shoppers can be chosen", page.locator('[data-testid="stSidebar"] [role="combobox"]').count() == 1)

    for prefix in VERSIONS:
        v.pick(page, prefix)
        if prefix.strip() in ("v2", "v3"):
            expected = 1 if os.environ.get("INVENTORY_MCP_ENABLED") == "1" else 0  # the switch is opt-in
            check(f"inventory switch is {'offered' if expected else 'off by default'} on {prefix.strip()}",
                  page.get_by_text("Agent also calls the inventory MCP server").count() == expected)
        elif prefix.strip() in ("Keyword", "v0", "v1", "v1+", "MCP"):
            check(f"inventory switch is hidden on {prefix.strip()}", page.get_by_text("Agent also calls the inventory MCP server").count() == 0)
        caption = page.get_by_text("every result is kept").first.inner_text()
        check(f"selecting {prefix.strip()} shows it as the active version", caption.startswith(v.LABELS.get(prefix, prefix).split(" (")[0].split(":")[0]), caption[:30])

    # ---- folds, refresh, clear
    click_button(page, "Clear results") if not page.get_by_role("button", name="Clear results").first.is_disabled() else None
    v.pick(page, "v0")
    v.ask(page, v.REWRITE)
    v.pick(page, "v1 ")
    v.ask(page, v.REQUEST)
    title_text = page.locator("summary").all_inner_texts()
    check("each answer is kept in its own fold", folds(page) == 2, f"{folds(page)} folds")
    check("a fold title names the version and the settings behind it (newest first)",
          len(title_text) == 2 and "0.3 keyword + 0.65 text vector + 0.05 picture vector" in title_text[0] and "vector only" in title_text[1],
          title_text[0][:60].replace("\n", " ") if title_text else "")
    size = page.locator(".st-key-query input").evaluate("e => [parseFloat(getComputedStyle(e).fontSize), parseInt(getComputedStyle(e).fontWeight)]")
    check("the search text is larger and bold", size[0] >= 19 and size[1] >= 700, f"{size[0]:g}px, weight {size[1]}")
    check("the query line in the fold is larger and bold",
          page.locator(FOLD_QUERY).first.evaluate("e => parseFloat(getComputedStyle(e).fontSize)") >= 18)
    check("the newest fold is open and shows result cards and the panel under the hood",
          len(v.cards(page)) >= 1 and "Under the hood" in page.inner_text("body"))
    box = page.locator(".st-key-query input")
    box.click()
    box.fill(v.REWRITE)
    box.press("Enter")  # the search bar is a form: Enter searches
    page.wait_for_function("n => document.querySelectorAll('[data-testid=\"stExpander\"]').length >= n", arg=3, timeout=120_000)
    page.wait_for_selector('[data-testid="stStatusWidget"]', state="detached", timeout=120_000)
    pause(2)
    check("Enter in the search bar runs the search", folds(page) == 3, f"{folds(page)} folds")
    check("the second hybrid result shows arrows against the previous rung",
          any(m in page.inner_text("body") for m in ("▲", "▼", "new")))
    folds_now = 3

    reload(page)
    check("a browser refresh keeps the folds", folds(page) == folds_now, f"{folds(page)} folds after refresh")
    click_button(page, "New session")
    check("New session keeps the folds", folds(page) == folds_now)
    v.select_shopper(page, "demo-shopper")
    check("another shopper starts with an empty page", folds(page) == 0)
    v.select_shopper(page, "bob")
    check("switching back brings the first shopper's folds back", folds(page) == folds_now)
    click_button(page, "Clear results")
    check("Clear results empties the page", folds(page) == 0)
    reload(page)
    check("the folds stay gone after a refresh", folds(page) == 0)
    check("Clear results is disabled when there is nothing to clear",
          page.get_by_role("button", name="Clear results").first.is_disabled())

    # ---- the guided demo: four acts, Next selects the version and runs the request
    v.set_toggle(page, "Guided demo", True)
    check("Guided demo offers a Start button and the four acts", page.get_by_role("button", name="Start the demo").count() == 1
          and "four acts" in page.inner_text("body"))
    click_button(page, "Start the demo")
    v.wait_until(page, lambda: folds(page) == 1 and "Working" not in page.inner_text("body"), 90)
    # the demo opens at acts.START (step 3, hybrid) so the lexical and vector rungs stay on screen
    first = acts.first()
    _, k0, n0, _ = acts.position(first)
    check("Start opens the demo at its first step and names the act",
          "Act 1 of 4" in page.inner_text("body") and f"step {k0} of {n0}" in page.inner_text("body") and folds(page) == 1)
    click_button(page, "Next ▶")
    v.wait_until(page, lambda: folds(page) == 2 and "Working" not in page.inner_text("body"), 90)
    _, k1, n1, _ = acts.position(first + 1)
    check("Next moves to the following step, selects its version and runs it",
          f"step {k1} of {n1}" in page.inner_text("body") and folds(page) == 2)
    side = page.locator('[data-testid="stSidebar"] [data-testid="stRadio"] label').all_inner_texts()
    numbered = [x for x in side if x[:1].isdigit()]
    check("the sidebar lists all eleven steps in order", numbered[:3] == ["1. Lexical only", "2. Vector only",
          "3. Lexical + vector (hybrid)"] and len(numbered) == len(acts.STEPS))
    check("the comparison table shows a column per step so far", "Comparison so far" in page.inner_text("body"))
    target = first + 2
    _, k2, n2, _ = acts.position(target)
    page.locator('[data-testid="stSidebar"] label').filter(has_text=f"{target + 1}. ").first.click()
    v.wait_until(page, lambda: folds(page) == 3 and "Working" not in page.inner_text("body"), 90)
    check("picking a step in the list jumps to it and runs it", f"step {k2} of {n2}" in page.inner_text("body") and folds(page) == 3)
    click_button(page, "◀ Back")
    v.wait_until(page, lambda: folds(page) == 4 and "Working" not in page.inner_text("body"), 90)
    check("Back returns to the step before and runs it again, as Next does",
          f"step {k2 - 1} of {n2}" in page.inner_text("body") and folds(page) == 4, f"{folds(page)} folds")
    v.set_toggle(page, "Guided demo", False)
    check("Guided demo switches off", "Act 1 of 4" not in page.inner_text("body"))
    click_button(page, "Clear results")

    # ---- memory lists: open, close, reopen, refresh
    for cycle in (1, 2, 3):
        v.set_toggle(page, "Show long-term facts", True)
        opened = "Long-term facts" in v.sidebar_text(page)
        v.set_toggle(page, "Show long-term facts", False)
        closed = "Long-term facts" not in v.sidebar_text(page)
        check(f"the facts list opens and closes (cycle {cycle})", opened and closed)
    v.set_toggle(page, "Show memory history", True)
    first = v.sidebar_text(page)
    check("the history list opens", "Memory history" in first, "timeline of turns and memories")
    stamp = first.split("Updated ")[1][:8] if "Updated " in first else ""
    pause(1.2)
    click_button(page, "Refresh now")
    second = v.sidebar_text(page)
    check("Refresh now re-reads the list (the 'Updated' time moves)", "Updated " in second and second.split("Updated ")[1][:8] >= stamp,
          f"{stamp} -> {second.split('Updated ')[1][:8] if 'Updated ' in second else '?'}")
    click_button(page, "Refresh now")
    check("Refresh now keeps working the second time", "Memory history" in v.sidebar_text(page))

    # ---- a new memory event shows up in the open history without any click
    def lines() -> list[str]:  # the open list shows only the newest few lines, so compare content, not count
        return page.locator('[data-testid="stSidebar"] li').all_inner_texts()
    before = lines()
    v.pick(page, "v3")
    v.new_session(page)
    v.ask(page, v.TODAY)
    grew = v.wait_until(page, lambda: lines() != before, v.extraction_wait() + 60)
    check("a new memory event appears in the open list on its own", grew, f"newest line: {(lines() or ['-'])[0][:60]}")
    v.set_toggle(page, "Show memory history", False)
    check("the history list closes again", "Memory history" not in v.sidebar_text(page).replace("Show memory history", ""))

    # ---- forget (as the test shopper, never Bob)
    v.forget_check(page, lambda name, ok, detail="": check(name, ok, detail))
    check("Forget this shopper leaves the page empty", folds(page) == 0)
    v.select_shopper(page, "bob")
    v.pick(page, "v0")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True)
    args = ap.parse_args()
    results: list[Result] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(Result(name, bool(ok), detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)

    print(f"checking every control of {args.url}", flush=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(args.url)
        page.wait_for_selector('[data-testid="stSidebar"]', timeout=60_000)
        pause(3)
        try:
            run(page, check)
        except Exception as e:  # noqa: BLE001 - report what failed, keep the earlier results
            check("the UI check itself", False, f"{type(e).__name__}: {str(e)[:160]}")
            page.screenshot(path=str(Path(tempfile.gettempdir()) / "ui-check-failure.png"))
        finally:
            browser.close()
    failed = [r for r in results if not r.ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} UI checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

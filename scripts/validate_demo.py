#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Walk every demo use case through a real browser and report pass or fail: a readiness check before a demonstration.

Each check types what the guided demo tells the demonstrator to type and asserts what it says will happen
(the T-rex bank's rank on the retrieval ladder: vector, keyword, hybrid, hybrid + rerank, the agent's thinking, the MCP
protocol steps, the agent as an MCP client, Today with and without memory, the budget change, and forgetting a test
shopper). The lines it types are the ones in retail_agent_demo/acts.py, the same the guided demo uses.

It drives the app at --url (default http://localhost:8502, the EC2 app behind scripts/app_tunnel.sh; use
http://localhost:8501 for the local one). The memory checks WRITE to Bob's memory, so the script resets him afterwards
(unless --no-reset). Needs: pip install playwright && playwright install chromium.

Usage: python scripts/validate_demo.py [--url http://localhost:8502] [--no-forget] [--no-reset]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retail_agent_demo import proc  # noqa: E402
from retail_agent_demo.acts import BUDGET, FORGET_LINE, REQUEST, REWRITE, STOCK, TODAY  # noqa: E402
from retail_agent_demo.waits import pause  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TREX = "MASSJOY"


@dataclass
class Result:
    name: str
    ok: bool
    detail: str


FOLDS = '[data-testid="stExpander"]'


def ask(page: Page, text: str, timeout_ms: int = 240_000) -> str:
    """Type into the search bar, press Search, wait for the new result fold; return the page text."""
    before = page.locator(FOLDS).count()
    box = page.locator(".st-key-query input")
    box.click()
    box.fill(text)
    page.get_by_role("button", name="Search", exact=True).click()
    page.wait_for_function("n => document.querySelectorAll('[data-testid=\"stExpander\"]').length >= n",
                           arg=before + 1, timeout=timeout_ms)
    page.wait_for_selector('[data-testid="stStatusWidget"]', state="detached", timeout=timeout_ms)
    pause(2)
    return page.inner_text("body")


def cards(page: Page) -> list[str]:
    """The result cards of the newest fold (the first one, newest first), in rank order."""
    return page.locator(FOLDS).first.locator('[class*="st-key-card-"]').all_inner_texts()


LABELS = {"Keyword": "Lexical only", "v0": "Vector only", "v1 ": "Lexical + vector (hybrid)", "v1+": "Lexical + vector + rerank",
          "v2": "Agent (hybrid search), no memory", "MCP": "MCP: outside client", "v3": "Agent (hybrid search) + memory"}


def pick(page: Page, label: str) -> None:
    """Choose a version by its short key (Keyword, v0, v1 , v1+, v2, MCP, v3) or by any text of its label."""
    page.locator('[data-testid="stSidebar"] label').filter(has_text=LABELS.get(label, label)).first.click()
    pause(1.5)


def new_session(page: Page) -> None:
    page.get_by_role("button", name="New session").click()
    pause(1)


def rank_of(page: Page, needle: str) -> int:
    return next((i for i, text in enumerate(cards(page), 1) if needle in text), 0)


def ask_until(page: Page, text: str, expect: str, tries: int = 4) -> str:
    """The service drops an agent's trace records on roughly one run in five; ask again in a new session."""
    body = ask(page, text)
    for _ in range(tries - 1):
        if expect in body:
            break
        new_session(page)
        body = ask(page, text)
    return body


def select_shopper(page: Page, name: str) -> None:
    """Open the shopper dropdown and pick a name. After the memory checks the sidebar is long and scrolled, and the popup
    does not always open on the first click, so scroll to the top and retry."""
    for _ in range(4):
        page.evaluate("""() => { const s = document.querySelector('[data-testid="stSidebarContent"]');
                               if (s) s.scrollTop = 0; }""")
        pause(0.5)
        page.locator('[data-testid="stSidebar"] [role="combobox"]').first.click()
        try:
            page.get_by_role("option", name=name).click(timeout=4000)
            pause(2)
            return
        except Exception:  # noqa: BLE001 - the popup did not open; close it and try again
            page.keyboard.press("Escape")
            pause(1)
    raise RuntimeError(f"could not select shopper {name!r}")


def extraction_wait() -> int:
    """Seconds to let memory extraction finish: OpenSearch Memory's takes about 15 s (it waits up to 10 s for the next message
    before it extracts)."""
    return 40


def set_toggle(page: Page, label: str, on: bool = True) -> None:
    """Switch a sidebar toggle to the wanted state (a click on a toggle that is already in that state would undo it)."""
    switch = page.get_by_role("switch", name=label)
    if switch.is_checked() != on:
        page.locator('[data-testid="stSidebar"] label').filter(has_text=label).first.click()
    pause(3)


def wait_until(page: Page, predicate, seconds: int) -> bool:
    """Memory lists refresh every 10 s while open and extraction is asynchronous: poll instead of guessing one delay."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        pause(5)
    return predicate()


def sidebar_text(page: Page) -> str:
    return page.locator('[data-testid="stSidebar"]').inner_text()


def facts_listed(page: Page) -> int:
    return page.locator('[data-testid="stSidebar"] li').count()


def forget_check(page: Page, check) -> None:
    select_shopper(page, "demo-shopper")
    new_session(page)
    ask(page, FORGET_LINE)
    set_toggle(page, "Show long-term facts", True)
    had = wait_until(page, lambda: facts_listed(page) > 0, extraction_wait() + 60)
    page.get_by_role("button", name="Forget this shopper").click()
    # the Serverless forget keeps watching for 30 s after the last delete, so the counts appear half a minute later
    # (the button's own label contains "memories", so wait for a word that only the result has)
    word = "left over"
    gone = wait_until(page, lambda: word in sidebar_text(page), 120)  # the counts of what was deleted
    empty = wait_until(page, lambda: facts_listed(page) == 0 and "Nothing is remembered" in sidebar_text(page), 60)
    set_toggle(page, "Show long-term facts", False)
    check("forget a test shopper: facts existed, the counts are shown, nothing is left", had and gone and empty)


def run_checks(page: Page, forget: bool, results: list[Result], only_forget: bool = False) -> None:

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(Result(name, bool(ok), detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)

    if only_forget:
        pick(page, "v3")
        forget_check(page, check)
        return

    # Act 1, the retrieval ladder: one request through four retrievers; the T-rex bank is the product to follow
    pick(page, "v0")
    ask(page, REQUEST)
    r = rank_of(page, TREX)
    check("vector, the shopper's own sentence: the T-rex bank is 2nd (a prototype looks fine)", r == 2, f"rank {r}")
    ask(page, REWRITE)
    r = rank_of(page, TREX)
    check("vector, the model's rewrite: the T-rex bank falls to 4th", r == 4, f"rank {r}")

    pick(page, "Keyword")
    ask(page, REWRITE)
    r = rank_of(page, TREX)
    check("keyword only: five cards, the keyword query under the hood, and the T-rex bank is not in the top 5",
          len(cards(page)) == 5 and "Keyword (lexical) query" in page.inner_text("body") and r == 0, f"T-rex rank {r}")

    pick(page, "v1 ")
    ask(page, REWRITE)
    r = rank_of(page, TREX)
    body = page.inner_text("body")
    check("hybrid, the same rewrite: the T-rex bank stays 4th (keyword never found it), fusion shown",
          r == 4 and "Fusion" in body and page.locator('[data-testid="stJson"]').count() > 0,
          f"rank {r}")

    pick(page, "v1+")
    ask(page, REWRITE)
    r = rank_of(page, TREX)
    ladder = page.inner_text("body")
    check("hybrid + rerank: the T-rex bank climbs into the top two and the ladder lists all four rungs",
          r in (1, 2) and "Rank ladder" in ladder and all(k in ladder for k in ("Lexical", "Vector", "Lexical + vector", "+ Rerank")), f"rank {r}")

    pick(page, "v2")
    body = ask_until(page, REQUEST, "Documents the agent retrieved")
    check("v2 agent: a prose answer with a price, its thinking under the hood, and the documents it retrieved as cards and a table",
          "The agent runs the search" in body and "$" in body
          and "The agent searched for:" in body and "Documents the agent retrieved" in body and len(cards(page)) >= 1)

    pick(page, "MCP")
    body = ask(page, REWRITE)
    check("MCP outside client: initialize, tools/list, tools/call and five products",
          all(s in body for s in ("initialize", "tools/list", "tools/call")) and len(cards(page)) == 5)

    pick(page, "v2")
    switch = page.get_by_text("Agent also calls the inventory MCP server")
    if switch.count():
        switch.first.click()
        pause(2)
        new_session(page)
        body = ask_until(page, STOCK, "Asks the inventory server")
        check("agent as MCP client: check_stock and delivery_estimate run on the external server",
              "Asks the inventory server: check_stock" in body and "Columbus" in body, "warehouse data in the answer")
        switch.first.click()
        pause(2)
    elif os.environ.get("INVENTORY_MCP_ENABLED") == "1":
        check("agent as MCP client", False, "the inventory switch is not shown (INVENTORY_MCP_ENABLED=1 and agent_runtime_arn?)")
    else:
        print("skipped: agent as MCP client (the optional inventory server is off; set INVENTORY_MCP_ENABLED=1 to include it)")

    new_session(page)
    body = ask(page, TODAY)
    check("Today without memory (v2): the agent has no history",
          any(w in body for w in ("don't have any record", "no record", "don't have access", "no access", "can't see", "not able to", "don't have any notes", "no notes", "don't have any information", "starts fresh")))

    pick(page, "v3")
    new_session(page)
    body = ask(page, TODAY)
    check("Today with memory (v3): answers from Sunday's notes and stores the turn",
          any(w in body.lower() for w in ("massjoy", "t-rex", "trex", "tyrannosaurus", TREX.lower())) and "Conversation stored" in body)

    body = ask(page, BUDGET)
    check("the $40 answer offers a dearer dinosaur bank (the added $38.99 one, or the $33.19 one)",
          any(w in body.lower() for w in ("roaring", "light-up", "38.99", "33.19", "musical", "stealing")))
    set_toggle(page, "Show long-term facts", True)
    set_toggle(page, "Show memory history", True)
    # the open lists refresh themselves every 10 s; extraction takes seconds
    seen = wait_until(page, lambda: "40" in sidebar_text(page), extraction_wait() + 90)
    sidebar = sidebar_text(page)
    check("budget $30 -> $40: the facts and the memory history both list it", seen and "Long-term facts" in sidebar
          and "Memory history" in sidebar, "lists open, refreshed on their own")
    set_toggle(page, "Show memory history", False)
    set_toggle(page, "Show long-term facts", False)
    hidden = sidebar_text(page)
    check("the memory lists close again", "Long-term facts" not in hidden and "Memory history" not in hidden.replace("Show memory history", ""))

    if forget:
        forget_check(page, check)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8502")
    ap.add_argument("--no-forget", action="store_true", help="skip the forget check (it switches to demo-shopper)")
    ap.add_argument("--only-forget", action="store_true", help="run just the forget check")
    ap.add_argument("--no-reset", action="store_true", help="do not reset Bob afterwards")
    args = ap.parse_args()
    print(f"validating {args.url}", flush=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(args.url)
        page.wait_for_selector('[data-testid="stSidebar"]', timeout=60_000)
        pause(2)
        results: list[Result] = []
        try:
            run_checks(page, not args.no_forget, results, args.only_forget)
        except Exception as e:  # noqa: BLE001 - keep the earlier results, report this, still reset Bob below
            results.append(Result("the validation run itself", False, f"{type(e).__name__}: {str(e)[:160]}"))
            print("  FAIL  the validation run itself  " + " / ".join(str(e).splitlines()[:4])[:400], flush=True)
        finally:
            browser.close()
    if not args.no_reset:
        script = str(ROOT / "scripts/sls_reset_demo_memory.py")
        proc.run([sys.executable, script, "--user", "bob"], check=True, cwd=ROOT, capture_output=True)
        print("Bob reset to the Sunday state")
    failed = [r for r in results if not r.ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

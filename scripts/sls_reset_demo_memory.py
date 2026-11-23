#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Serverless track: forget a shopper in OpenSearch Memory, then store the scripted Sunday conversation for them.

After this: Sunday happened, today has not, and the budget record says 30 dollars.
Usage (under your login tooling): python scripts/sls_reset_demo_memory.py --user bob [--no-seed]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from retail_agent_demo import agentcore as ac  # noqa: E402
from retail_agent_demo import osmemory as osm  # noqa: E402
from retail_agent_demo.acts import SUNDAY  # noqa: E402
from retail_agent_demo.config import load_settings  # noqa: E402
from retail_agent_demo.waits import pause  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="shopper id to reset (destructive for that shopper)")
    ap.add_argument("--no-seed", action="store_true", help="forget only; skip the Sunday seed")
    ap.add_argument("--show-text", action="store_true", help="print the text of each memory (only for the scripted demo shopper)")
    args = ap.parse_args()

    s = load_settings()
    client = osm.new_client(s.ids.get("memory_region", "us-east-1"))
    space_id = s.id("space_id")

    # settle: a turn stored seconds ago (the budget change to $40) is not searchable yet, so it is not deleted, and its extraction still
    # writes a memory afterwards; watch for 30 s after the last delete so that nothing of today comes back.
    print(f"forgot '{args.user}':", osm.forget_shopper_verified(client, space_id, args.user, settle=30))
    if args.no_seed:
        return
    # Extraction is a model run: now and then it leaves the budget out, and a late extraction of the day's run can bring the $40 back.
    # Sunday is scripted, so seed again (after forgetting) until the $30 budget is a memory and no memory says 40, up to three times.
    facts: list = []
    for attempt in range(1, 4):
        osm.remember_turn(client, space_id, args.user, ac.new_session_id(), SUNDAY)
        print(f"Sunday stored (attempt {attempt}); waiting for OpenSearch Memory to extract memories (usually 10 to 30 s)…")
        deadline = time.time() + 60
        while time.time() < deadline:
            pause(5)
            facts = osm.list_facts(client, space_id, args.user)
            if any("30" in f["memory"] for f in facts):
                break
        pause(15)  # a stray memory of the day's run, if there is one, shows up now
        facts = osm.list_facts(client, space_id, args.user)
        has30, has40 = (any(n in f["memory"] for f in facts) for n in ("30", "40"))
        if (has30 and not has40) or attempt == 3:
            break
        print("  " + ("a memory mentions 40" if has40 else "no memory mentions the 30 dollar budget") + "; forgetting and seeding again")
        osm.forget_shopper_verified(client, space_id, args.user, settle=30)
    print(f"{len(facts)} memories:")
    for f in facts:
        # Memory text is printed only with --show-text: memories are model-extracted from conversations and can hold personal
        # data. The demo shopper's conversation is scripted, synthetic text; for any other shopper print counts only.
        print(" -", f["strategy_type"], ":", f["memory"][:200] if args.show_text else f"({len(f['memory'])} characters)")
    if any("40" in f["memory"] for f in facts):
        print("WARNING: a memory mentions 40 - today's state leaked; re-run the reset.")
    elif not any("30" in f["memory"] for f in facts):
        print("WARNING: no memory mentions the 30 dollar budget yet; extraction can take a few minutes. Check before the demo.")
    else:
        print("OK: the budget record says 30.")


if __name__ == "__main__":
    main()

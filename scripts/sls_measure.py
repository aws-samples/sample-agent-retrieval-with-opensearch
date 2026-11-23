#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Measure the Serverless track's own behaviour, for the docs (one run each; a smoke test, not a benchmark).

  1. The typed search tool: does the hosted agent send the right price limits? (the shopper's request, fresh sessions)
  2. OpenSearch Memory: how long until Sunday's memories exist, how many, and what a changed budget does to them
  3. Rerank: does the in-engine `rerank` processor score with the Bedrock Rerank connector, and how long does it take?

Writes data/sls-measurements.json and docs/results/serverless-measurements.md. Uses the throwaway shopper `sls-measure`
and forgets it at the end. Run under your login tooling with and the SSM port forward open.

Usage: python scripts/sls_measure.py [--agent-runs 8]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import date
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from retail_agent_demo import agentcore as ac  # noqa: E402
from retail_agent_demo import osmemory as osm  # noqa: E402
from retail_agent_demo import search  # noqa: E402
from retail_agent_demo.acts import SUNDAY  # noqa: E402
from retail_agent_demo.config import load_settings  # noqa: E402
from retail_agent_demo.os_client import OSClient  # noqa: E402
from retail_agent_demo.waits import pause  # noqa: E402

ACTOR = "sls-measure"
REQUEST = ("My nephew turns six next week and he loves dinosaurs. I'd like a gift that gets him saving his pocket money. "
           "My budget is 30 dollars.")  # the shopper's request, with no name to type
REWRITE = "dinosaur piggy bank money bank kids under 30 dollars"
TREX = "B07QRXG93G"


def measure_tool(rt, arn: str, runs: int) -> dict:
    rows = []
    for _ in range(runs):
        r = ac.invoke_agent(rt, arn, REQUEST, ac.new_session_id())
        calls = [json.loads(st["input"]) for st in r.steps if st["tool"] == "run_product_search"]
        ok = bool(calls) and all(float(c.get("max_price", 100000)) == 30 and float(c.get("min_price", 0)) == 0 for c in calls)
        extra = sorted({k for c in calls for k in c} - {"query", "min_price", "max_price"})
        rows.append({"ok": ok, "searches": len(calls), "extra_keys": extra, "seconds": r.seconds,
                     "mentions_trex": TREX in json.dumps(r.steps) or "T-Rex" in r.answer or "T-rex" in r.answer})
        print(f"  run: price range right={ok} searches={len(calls)} {r.seconds}s", flush=True)
    return {"runs": runs, "price_range_right": sum(x["ok"] for x in rows), "extra_keys_seen": sorted({k for x in rows for k in x["extra_keys"]}),
            "median_seconds": round(statistics.median(x["seconds"] for x in rows), 1),
            "trex_found": sum(x["mentions_trex"] for x in rows)}


def wait_until(predicate, seconds: int, every: int = 10):
    t0 = time.time()
    while time.time() - t0 < seconds:
        v = predicate()
        if v:
            return round(time.time() - t0), v
        pause(every)
    return None, None


def measure_memory(mc, space_id: str) -> dict:
    osm.forget_shopper(mc, space_id, ACTOR)
    osm.remember_turn(mc, space_id, ACTOR, ac.new_session_id(), SUNDAY)
    secs, facts = wait_until(lambda: [f for f in osm.list_facts(mc, space_id, ACTOR)] or None, 240)
    print(f"  Sunday: first memories after {secs}s ({len(facts or [])})", flush=True)
    pause(20)  # let the extraction finish, then count
    before = osm.list_facts(mc, space_id, ACTOR)
    budget_before = [f["memory"] for f in before if re.search(r"\b30\b", f["memory"])]
    second = ac.new_session_id()
    osm.remember_turn(mc, space_id, ACTOR, second, [
        {"role": "user", "text": "My budget is 40 dollars now, my sister is chipping in. Which dinosaur piggy bank would you suggest?"},
        {"role": "assistant", "text": "Great, with 40 dollars there are more dinosaur banks to choose from."}])
    secs2, after = wait_until(lambda: (lambda fs: fs if any(re.search(r"\b40\b", f["memory"]) for f in fs) else None)(
        osm.list_facts(mc, space_id, ACTOR)), 240)
    pause(20)
    after = osm.list_facts(mc, space_id, ACTOR)
    # A record that says 40 may mention the old 30 as history ("updated from $30 to $40"); only a record that says 30
    # and not 40 is a stale budget.
    stale = [f["memory"] for f in after if re.search(r"\b30\b", f["memory"]) and not re.search(r"\b40\b", f["memory"])]
    fresh = [f["memory"] for f in after if re.search(r"\b40\b", f["memory"])]
    out = {"sunday_first_record_seconds": secs, "records_after_sunday": len(before),
           "by_strategy": {k: sum(1 for f in before if f["strategy_type"] == k) for k in sorted({f["strategy_type"] for f in before})},
           "budget_records_with_30_before": len(budget_before), "budget_change_seconds": secs2,
           "records_after_budget_change": len(after), "records_with_40": len(fresh), "stale_30_records_left": len(stale),
           "stale_30_text": stale[:3], "fresh_40_text": fresh[:3]}
    print("  memory:", json.dumps({k: v for k, v in out.items() if "text" not in k}), flush=True)
    print("  forgot:", osm.forget_shopper(mc, space_id, ACTOR), flush=True)
    return out


def measure_rerank(client: OSClient, s) -> dict:
    index, emb = s.ids["products_index"], s.id("embedding_model_id")
    lanes = s.ids.get("rerank_lanes") or [{"pipeline": s.ids["hybrid_rerank_pipeline"], "region": s.rerank_region}]
    out = {"in_engine": {}}
    for lane in lanes:
        t = time.time()
        try:
            top = search.run_v1_rerank(client, index, emb, lane["pipeline"], REWRITE, REWRITE, 0, 30)
            ranks = [p.product_id for p in top]
            out["in_engine"][lane["region"]] = {"works": True, "seconds": round(time.time() - t, 1),
                                                "trex_rank": ranks.index(TREX) + 1 if TREX in ranks else None,
                                                "top": [p.title[:40] for p in top[:3]]}
        except OSError as e:
            out["in_engine"][lane["region"]] = {"works": False, "error": str(e)[:300]}
        print(f"  rerank {lane['region']}: {out['in_engine'][lane['region']]}", flush=True)
    return out


def report(data: dict) -> str:
    t, m, r = data["tool"], data["memory"], data["rerank"]["in_engine"]
    lines = [f"# Serverless track: measured behaviour ({data['date']})", "",
             "One run each on the demo account, a smoke test and not a benchmark. Script: `scripts/sls_measure.py`.", "",
             "## The typed search tool (hosted agent, the shopper's request, fresh sessions)", "",
             f"- Price range sent correctly (0 to 30 dollars): **{t['price_range_right']} of {t['runs']}** runs.",
             f"- Keys other than query, min_price and max_price: {', '.join(t['extra_keys_seen']) or 'none'} (the tool's schema allows no others).",
             f"- The T-rex bank was in the agent's search results in {t['trex_found']} of {t['runs']} runs.",
             f"- Median time for the whole turn (model, search, answer): {t['median_seconds']} s.", "",
             "## OpenSearch Memory (one prompt-based strategy with consolidation; the budget is written by the app and updated in place)", "",
             f"- Sunday's four turns became **{m['records_after_sunday']} memories** ("
             + ", ".join(f"{v} {k}" for k, v in m["by_strategy"].items()) + f"); the first one appeared after **{m['sunday_first_record_seconds']} s**.",
             f"- After \"my budget is 40 dollars now\": {m['records_after_budget_change']} memories in total, " +
             f"{m['records_with_40']} mention 40, **{m['stale_30_records_left']} still say 30 without 40** (the app updated the budget memory in place).",
             f"- The change showed up after {m['budget_change_seconds']} s.",
             "- There is no change log: the app's memory history is a timeline of turns stored and memories created or updated; a replaced or deleted memory leaves nothing behind.", "",
             "## Rerank", ""]
    for region, v in r.items():
        lines.append(f"- {region}: " + (f"**works in the engine** ({v['seconds']} s; the T-rex bank is rank {v['trex_rank']} for the rewrite, reranked)" if v.get("works") else f"failed: {v.get('error', '')[:200]}"))
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent-runs", type=int, default=8)
    ap.add_argument("--skip", nargs="*", default=[], choices=["tool", "memory", "rerank"])
    ap.add_argument("--report-only", action="store_true", help="write the markdown report from data/sls-measurements.json")
    args = ap.parse_args()
    out_md = ROOT / "docs/results/serverless-measurements.md"
    if args.report_only:
        out_md.write_text(report(json.loads((ROOT / "data/sls-measurements.json").read_text())))
        print("wrote", out_md)
        return
    s = load_settings()
    rt = boto3.client("bedrock-agentcore", region_name=s.region)
    path = ROOT / "data/sls-measurements.json"
    data: dict = json.loads(path.read_text()) if path.exists() else {}  # sections skipped now keep their last values
    data["date"] = date.today().isoformat()
    if "tool" not in args.skip:
        print("typed search tool", flush=True)
        data["tool"] = measure_tool(rt, s.ids["agent_runtime_arn"], args.agent_runs)
    if "memory" not in args.skip:
        print("OpenSearch Memory", flush=True)
        data["memory"] = measure_memory(osm.new_client(s.ids.get("memory_region", "us-east-1")), s.id("space_id"))
    if "rerank" not in args.skip:
        print("rerank", flush=True)
        data["rerank"] = measure_rerank(OSClient(s, timeout=60), s)
    (ROOT / "data").mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    out_md.write_text(report(data))
    print(json.dumps(data, indent=2)[:3000])


if __name__ == "__main__":
    main()

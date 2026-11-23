# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the inventory MCP server answered, as table rows, and why an agent turn has no search ranking.

The agent's steps (agent.tool_steps) carry the external calls check_stock and delivery_estimate with their JSON result.
Pure functions, unit-tested."""

from __future__ import annotations

import json
import re

TOOLS = ("check_stock", "delivery_estimate")


def _json(text) -> dict:
    try:
        data = json.loads(text) if isinstance(text, str) else text
        if isinstance(data, list) and data and isinstance(data[0], dict) and "text" in data[0]:  # a raw MCP content block
            data = json.loads(data[0]["text"])
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def rows(steps: list[dict], products: list[dict]) -> list[dict]:
    """One row per product the inventory server was asked about: {id, product, stock, warehouse, days}, in the order asked."""
    titles = {p.get("id"): p.get("product") for p in products or []}
    out: dict[str, dict] = {}
    for step in steps or []:
        if step.get("tool") not in TOOLS:
            continue
        asked = _json(step.get("input"))
        answer = _json(step.get("output"))
        pid = answer.get("product_id") or asked.get("product_id")
        if not pid:
            continue
        row = out.setdefault(pid, {"id": pid, "product": titles.get(pid) or pid, "stock": "–", "warehouse": "–", "days": "–"})
        if step["tool"] == "check_stock" and "in_stock" in answer:
            qty = answer.get("quantity")
            row["stock"] = (f"in stock · {qty} units" if qty is not None else "in stock") if answer["in_stock"] else "out of stock"
        if answer.get("warehouse"):
            row["warehouse"] = answer["warehouse"]
        if answer.get("days") is not None:
            row["days"] = f"{answer['days']} days"
    return list(out.values())


def why_no_search(turn: dict) -> str | None:
    """None when the turn's search returned documents; otherwise a one-sentence reason there is no ranking for it."""
    if turn.get("products"):
        return None
    steps = turn.get("steps") or []
    tools = [s.get("tool") for s in steps]
    if "run_product_search" in tools:
        return "the search ran but returned no documents."
    if any(t in TOOLS for t in tools):
        return ("it asked the inventory server about stock and delivery for a product it already knew, so it did not search again "
                "(the table under the answer shows what the server said).")
    if any("not recorded" in n for n in turn.get("notes") or []):
        return "the service did not record this turn's tool steps, so the search (if any) is not shown."
    version = (turn.get("version") or "").lower()
    if "memory" in version and "no memory" not in version:
        recalled = re.search(r"(\d+) recalled", turn.get("config") or "")
        if recalled and int(recalled.group(1)) == 0:  # a shopper with nothing stored (for example just forgotten)
            return "it called no search tool, and there were no notes about this shopper to answer from."
        return "it answered from the notes remembered about this shopper (agentic memory), so no search was needed."
    return "it answered without calling its search tool: nothing in the question needed a product lookup."

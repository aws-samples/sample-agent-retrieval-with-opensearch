# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What an agent's search returned, for the result cards and the table in its fold.

The agents (the domain's conversational agent and the Serverless Strands agent) call a search tool and write an answer from
what it returns. These pure functions pull the hits and the query out of the tool call, say which hits the answer mentions,
and list where else the same product appeared in earlier results. No Streamlit, no network, unit-tested."""

from __future__ import annotations

import json
import re

from . import ranks
from .turns import version_kind

NAMES = {**ranks.NAMES, "v2": "Agent, no memory", "v3": "Agent + memory", "mcp": "MCP"}
_GENERIC = {"the", "new", "kids", "kid", "for", "and", "with", "mini", "large", "small", "big", "cute", "baby", "set", "pack",
            "piggy", "pig", "bank", "banks", "money", "coin", "coins", "saving", "savings", "box", "dinosaur", "animal", "toy", "toys",
            "gift", "resin", "ceramic", "plastic", "plush", "electronic", "green", "pink", "blue", "red"}


def parse_hits(response) -> list[dict]:
    """[{product_id, title, price, in_stock, score}] from a search tool's result: one JSON hit per line (the domain's
    tool), a JSON list of rows (the Serverless tool) or a single search response."""
    text = response if isinstance(response, str) else json.dumps(response)
    rows: list = []
    try:
        body = json.loads(text)
        rows = body if isinstance(body, list) else (body.get("hits", {}).get("hits", []) if isinstance(body, dict) and "hits" in body else [body])
    except ValueError:
        for line in text.splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    hits = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        src = r.get("_source", r)
        pid = r.get("_id") or src.get("product_id")
        if pid and src.get("title"):
            hits.append({"product_id": str(pid), "title": src["title"], "price": src.get("price"),
                         "in_stock": src.get("in_stock"), "score": r.get("_score") or 0.0})
    return hits


def query_of(tool_input) -> dict:
    """{'query': str, 'lo': float|None, 'hi': float|None}: the words and price limits the agent sent to the search."""
    try:
        body = json.loads(tool_input) if isinstance(tool_input, str) else dict(tool_input)
    except (ValueError, TypeError):
        return {"query": "", "lo": None, "hi": None}
    if "query" in body and isinstance(body["query"], str):  # the Serverless typed tool: query, min_price, max_price
        return {"query": body["query"], "lo": body.get("min_price"), "hi": body.get("max_price")}
    words, lo, hi = "", None, None  # the domain: the whole query DSL the planner wrote
    try:
        hybrid = body["query"]["query"]["hybrid"]
        words = hybrid["queries"][0]["multi_match"]["query"]
        price = (hybrid.get("filter") or {}).get("range", {}).get("price", {})
        lo, hi = price.get("gte"), price.get("lte")
    except (KeyError, IndexError, TypeError):
        pass
    return {"query": words, "lo": lo, "hi": hi}


def last_search(steps: list[dict]) -> dict | None:
    """The agent's last search call: {'query','lo','hi','hits'}, or None when it ran no search (or the trail lacks it)."""
    for step in reversed(steps):
        if step.get("tool") == "run_product_search" and step.get("hits"):
            q = query_of(step.get("raw_input") or step.get("input", ""))
            return {**q, "hits": step["hits"]}
    return None


def mentioned(title: str, answer: str) -> bool:
    """True when the answer names this product: a distinctive word from the start of its title (the brand, not 'piggy' or
    'bank') appears in the answer."""
    text = re.sub(r"[^a-z0-9 ]+", " ", answer.lower())
    for word in re.sub(r"[^A-Za-z0-9 ]+", " ", title).split()[:5]:
        if len(word) >= 4 and word.lower() not in _GENERIC and f" {word.lower()} " in f" {text} ":
            return True
    return False


def earlier_positions(turns: list[dict], product_id: str, limit: int = 3) -> str:
    """Where this product sat in earlier saved results, newest first: 'Hybrid #3 · Vector #4' (empty if never seen)."""
    seen: list[str] = []
    names: set[str] = set()
    for turn in reversed(turns):
        name = NAMES.get(version_kind(turn["version"]), turn["version"])
        if name in names:  # the newest result of each version only: several turns on one version would repeat itself
            continue
        for row in turn.get("products") or []:
            if row.get("id") == product_id:
                seen.append(f"{name} #{row.get('#')}")
                names.add(name)
                break
        if len(seen) >= limit:
            break
    return " · ".join(seen)


def row_extras(hits: list[dict], answer: str, turns: list[dict]) -> dict[str, dict]:
    """{product id: {'used': bool, 'earlier': str}} for the table and the cards of an agent's fold."""
    return {h["product_id"]: {"used": mentioned(h["title"], answer), "earlier": earlier_positions(turns, h["product_id"])}
            for h in hits}

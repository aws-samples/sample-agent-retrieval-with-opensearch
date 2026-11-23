# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""'Under the hood': what a version actually sent to the search engine, and what an agent did, in words and as requests.

Every request shown is built by the same pure functions in search.py that run the search, so the panel cannot drift from
what was executed. Items are plain dictionaries {"title", "detail", "body"}; body is a request or a parsed tool input
(or None). No Streamlit, no network, unit-tested."""

from __future__ import annotations

import json

from . import search
from .turns import price_range, version_kind

WEIGHTS = "keyword 0.3, vector 0.7, min-max normalised"
WEIGHTS_PICTURES = "keyword 0.3, text vector 0.65, picture vector 0.05, min-max normalised"


def _clip(text: str, limit: int = 140) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _item(title: str, detail: str, body: dict | None = None) -> dict:
    return {"title": title, "detail": detail, "body": body}


def search_hood(version: str, query_text: str, lo: float, hi: float, model_id: str,
                rerank_fell_back: bool = False, size: int = 5,
                retrieval: search.Retrieval | None = None) -> list[dict]:
    """The steps of a plain search version (keyword, vector, hybrid, hybrid + rerank) for the panel."""
    kind = version_kind(version)
    rv = retrieval or search.Retrieval(index="")
    where = price_range(lo, hi)
    fields = ", ".join(search.KEYWORD_FIELDS)
    if kind == "keyword":
        return [
            _item("Keyword (lexical) query",
                  f"Matches the words you typed (BM25) over: {fields}. No meaning, no spelling tolerance.",
                  search.build_lexical_query(query_text, lo, hi, size)),
            _item("Price filter", f"{where}, inside the query."),
        ]
    if kind == "v0":
        return [
            _item("Vector query", f"Your words become one vector ({model_id}); the 50 nearest products are fetched.",
                  search.build_v0_query(query_text, model_id)),
            _item("Price filter afterwards", f"{where} is applied in the app after the 50 are fetched, so fewer than " +
                                             f"{size} may remain."),
        ]
    hybrid = search.build_hybrid_query(query_text, model_id, lo, hi, size=size, vector_fields=rv.vector_fields,
                                       image_model_id=rv.image_model_id)
    vectors = [_item("Vector part", f"The same text as a vector ({model_id}), nearest 50, against the title and bullet-point vectors.")]
    if rv.image_model_id:
        vectors.append(_item("Picture vector part", "The same text embedded by a multimodal model into the space the product "
                                                    "pictures live in: it matches what a picture shows (only products that have a picture)."))
    fusion = WEIGHTS_PICTURES if rv.image_model_id else WEIGHTS
    items = [
        _item("Keyword part", f"Matches the words over: {fields}."),
        *vectors,
        _item("Fusion", f"Scores are normalised and combined: {fusion}."),
        _item("Price filter", f"{where}, inside the query, so every slot goes to an in-range product.", hybrid),
    ]
    if kind == "v1+rerank":
        if rerank_fell_back:
            items.append(_item("Rerank skipped", "The rerank service was throttled, so plain hybrid results are shown."))
        else:
            items.append(_item("Rerank", "The fused candidates are sent to Cohere Rerank 3.5 on Amazon Bedrock, scored "
                                         "against your words, and the best are kept.", search.build_rerank_ext(query_text)))
    return items


def _parsed(text: str) -> dict | None:
    try:
        body = json.loads(text)
    except (TypeError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def agent_hood(steps: list[dict]) -> list[dict]:
    """The agent's thinking as a timeline built from its tool calls: what it planned, searched and asked outside."""
    out = []
    for s in steps:
        tool, given = s.get("tool", ""), s.get("input", "")
        body = _parsed(given)
        if s.get("external"):
            out.append(_item(f"Asks the inventory server: {tool}",
                             f"An external MCP server, outside OpenSearch. Input: {_clip(given, 80)}. "
                             f"Result: {_clip(s.get('output', ''), 120)}", body))
        elif tool == "plan_product_query":
            question = (body or {}).get("question", given)
            out.append(_item("The agent plans the query", f"It turns the request into a search query for the index: {_clip(question)}",
                             _parsed(s.get("output", ""))))
        elif tool == "run_product_search":
            if body:
                detail = (f"Typed tool call: search \"{body.get('query', '')}\", price "
                          f"{body.get('min_price', 0)} to {body.get('max_price', 'no limit')}. The app builds the hybrid query from these.")
                out.append(_item("The agent runs the search", detail, body))
            else:
                out.append(_item("The agent runs the search", "It sends the query it planned to the index and reads the hits.", body))
        else:
            out.append(_item(f"Tool: {tool}", _clip(given, 100), body))
    return out


def trace_hood(trace: list[dict]) -> list[dict]:
    """The MCP protocol steps of the outside client, as timeline items."""
    items = []
    for t in trace:
        body = {k: t[k] for k in ("request", "response") if k in t} or None  # the JSON-RPC messages, when they were kept
        items.append(_item(f"MCP {t['method']}", t.get("summary", ""), body))
    return items

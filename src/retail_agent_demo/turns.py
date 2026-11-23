# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Saved outputs of the demo app: what one turn produced and the configuration behind it.

Every answer the app gives is kept as a Turn and shown in a fold whose title names the version and the settings that
produced it (vector only, hybrid, rerank, the agent and its tools, memory), so the person demonstrating can scroll back and compare
outputs. Pure functions and plain dictionaries: no Streamlit, no network, unit-tested.
"""

from __future__ import annotations

from retail_agent_demo.timing import seconds_text

HYBRID = "hybrid: 0.3 keyword + 0.65 text vector + 0.05 picture vector, min-max"
RERANK = "rerank: Cohere Rerank 3.5 on Amazon Bedrock, top 5 kept"


def price_range(lo: float, hi: float) -> str:
    lo_s = f"${lo:,.0f}" if lo else "$0"
    hi_s = f"${hi:,.0f}" if hi < 100000 else "no upper limit"
    return f"{lo_s} to {hi_s}"


# The sidebar's names for the seven versions, by stable kind. The kinds (keyword, v0, v1, v1+rerank, v2, mcp, v3) are what the
# code and the saved results use; the labels say what each version does.
VERSION_LABELS = {
    "keyword": "Lexical only",
    "v0": "Vector only",
    "v1": "Lexical + vector (hybrid)",
    "v1+rerank": "Lexical + vector + rerank",
    "v2": "Agent (hybrid search), no memory",
    "mcp": "MCP: outside client (hybrid search)",
    "v3": "Agent (hybrid search) + memory",
}
VERSIONS = list(VERSION_LABELS.values())
AGENT_KINDS = ("v2", "v3")


def version_kind(version: str) -> str:
    """Which of the demo's versions a label names: keyword, v0, v1, v1+rerank, v2, mcp or v3. Understands the current labels
    and the earlier ones ("v0 — vector only", "Keyword only", ...), so results saved before the rename still load."""
    for kind, label in VERSION_LABELS.items():
        if version == label:
            return kind
    if version.startswith(("Keyword", "Lexical only")):
        return "keyword"
    if version.startswith("v1+"):
        return "v1+rerank"
    if version.startswith("MCP"):
        return "mcp"
    return version.split(" ")[0].lower()


def config_summary(version: str, lo: float = 0, hi: float = 100000, inventory: bool = False,
                   recalled: int = 0, rerank_fell_back: bool = False) -> str:
    """One line naming the configuration that produced an output, for the fold's title."""
    kind = version_kind(version)
    backend = "OpenSearch Serverless"
    filt = price_range(lo, hi)
    if kind == "keyword":
        parts = ["keyword (BM25) over title, bullets, description, brand, color", f"price {filt} inside the query"]
    elif kind == "v0":
        parts = ["vector only (Titan v2 embedding, k=50)", f"price {filt} applied after retrieval"]
    elif kind == "v1":
        parts = [HYBRID, f"price {filt} inside the query"]
    elif kind == "v1+rerank":
        parts = [HYBRID, f"price {filt} inside the query"]
        parts.append("rerank throttled: plain hybrid shown" if rerank_fell_back else RERANK)
    elif kind == "mcp":
        parts = ["outside MCP client", "AgentCore Gateway, Cognito token", "hybrid search tool"]
    else:  # v2 and v3: the conversational agent
        parts = ["Strands agent on AgentCore Runtime", "typed tool run_product_search"]
        if inventory:
            parts.append("+ inventory MCP tools")
        if kind == "v3":
            parts.append("OpenSearch Memory")
            parts.append(f"{recalled} recalled fact{'s' if recalled != 1 else ''}")
    return backend + " · " + " · ".join(parts)


def fold_label(turn: dict, limit: int = 56) -> str:
    """Markdown for the fold's title: number, version and the request, then the configuration in small grey text."""
    prompt = " ".join(str(turn["prompt"]).split())
    prompt = prompt if len(prompt) <= limit else prompt[: limit - 1] + "…"
    head = f"**{turn['n']} · {turn['version']}** · {prompt.replace('$', chr(92) + '$')}"
    taken = (turn.get("timing") or {}).get("total")
    tail = f" · took {seconds_text(taken)}" if taken is not None else ""
    return f"{head}  \n:small[:gray[{turn['config'].replace('$', chr(92) + '$')}{tail}]]"


def product_rows(products, moves: dict | None = None, image_for=None, extras: dict | None = None) -> list[dict]:
    """Saved rows for the result cards. 'move' is the rank change against the previous rung of the ladder (ranks.py);
    'extras' adds, for an agent's results, whether the answer names the product and where it sat in earlier results."""
    moves, extras = moves or {}, extras or {}
    return [{"#": p.rank, "id": p.product_id, "product": p.title[:70], "price": f"${p.price:.2f}" if p.price is not None else "n/a",
             "in stock": "✅" if p.in_stock else "❌", "move": moves.get(p.product_id, ""),
             "image": (image_for(p.product_id) or "") if image_for else "", **extras.get(p.product_id, {})} for p in products]


def next_number(saved: list[dict]) -> int:
    """The number for the next output: one more than the highest so far.

    Counting the list would repeat a number once the store drops its oldest outputs (it keeps the newest 60), and two
    outputs with one number collide on their card keys."""
    return max((t.get("n", 0) for t in saved), default=0) + 1


def new_turn(n: int, version: str, prompt: str, config: str, answer: str, products=None, steps=None, trace=None,
             notes=None, hood=None, moves=None, image_for=None, extras=None, searched=None, step=None, run=None,
             timing=None) -> dict:
    """A saved output. Only plain data, so it survives Streamlit reruns and can be shown again later.
    'hood' is the 'under the hood' panel (explain.py); older saved files without it still load."""
    return {"n": n, "version": version, "prompt": prompt, "config": config, "answer": answer,
            "products": product_rows(products or [], moves, image_for, extras), "steps": list(steps or []),
            "trace": list(trace or []), "notes": list(notes or []), "hood": list(hood or []),
            "searched": searched or None,  # for an agent: the words and price limits it sent to the search
            "step": step, "run": run,
            "timing": timing or None}  # {"total": seconds, "segments": [{"name", "seconds", "level"}]}; older results have none  # the guided-demo step that produced it and which guided run (for the comparison table)

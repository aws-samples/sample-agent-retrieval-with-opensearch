# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Amazon Bedrock AgentCore for the Serverless track: the agent runtime and the catalog-search Gateway.

OpenSearch Serverless has no agents and no built-in MCP server. These functions play those roles:

  * invoke_agent   runs the Strands agent hosted on AgentCore Runtime (the domain track's v2 agent)
  * GatewayHttp    lets the demo's MCP client talk to an AgentCore Gateway with a bearer token

The shopper's long-term memory is Amazon OpenSearch Memory, in osmemory.py.

Every function takes a boto3 client so tests can pass a stub.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

import requests

from . import agent_results, mcp_client, search


# --------------------------------------------------------------------------------------------- the agent runtime
@dataclass
class AgentReply:
    answer: str
    session_id: str
    steps: list[dict] = field(default_factory=list)
    seconds: float = 0.0
    timings: list[dict] = field(default_factory=list)  # the runtime's own model turns and tool calls: [{"name", "seconds"}]


def new_session_id() -> str:
    return str(uuid.uuid4())  # the runtime wants at least 33 characters


def clean_note(text: str) -> str:
    """The runtime's one-sentence note about a missing earlier visit, or an empty string when the model said NONE (or nothing)."""
    text = (text or "").strip()
    return "" if not text or text.upper().startswith("NONE") else text


def invoke_agent(client, runtime_arn: str, prompt: str, session_id: str, inventory: bool = False, no_notes: bool = False) -> AgentReply:
    """One turn with the hosted agent. The session id keeps the agent's conversation (and its microVM) together.

    `no_notes` says the app has nothing remembered about the shopper: the runtime then tells the agent to search for any product the
    message asks about, and opens the answer with a sentence saying there is no record when the message refers to an earlier visit."""
    payload = json.dumps({"prompt": prompt, "session_id": session_id, "inventory": inventory, "no_notes": no_notes}).encode()
    resp = client.invoke_agent_runtime(agentRuntimeArn=runtime_arn, runtimeSessionId=session_id, payload=payload,
                                       contentType="application/json", accept="application/json")
    body = json.loads(resp["response"].read())
    if body.get("error"):
        raise RuntimeError(f"agent runtime: {body['error']}")
    return AgentReply(answer=body.get("answer", ""), session_id=session_id, steps=body.get("steps", []),
                      seconds=body.get("seconds", 0.0), timings=body.get("timings", []))


def timings_from_metrics(summary: dict) -> list[dict]:
    """Model turns and tool calls in order, with their seconds, from a Strands event-loop summary (result.metrics.get_summary()).

    Each cycle of the loop is a trace whose children are the model call and the tool calls. Returns [] when the summary has no traces,
    so a Strands version that reports differently only loses the breakdown, never the answer."""
    out = []
    for cycle in (summary or {}).get("traces") or []:
        for child in cycle.get("children") or []:
            seconds = child.get("duration")
            if seconds is None:
                continue
            name = str(child.get("name") or "step")
            if name.startswith("Recursive call"):  # the next cycle, already listed as a cycle of its own: counting it would double the time
                continue
            if name.startswith("stream_messages"):
                name = "Model turn"
            elif name.startswith("Tool: "):
                name = name[len("Tool: "):]
            out.append({"name": name.split("___", 1)[-1], "seconds": round(float(seconds), 3)})
    return out


_WARM = {"at": 0.0}
WARM_EVERY = 240.0  # seconds between warm-up calls; an idle container is recycled after some minutes


def warm_up(client, runtime_arn: str, now=time.monotonic) -> float | None:
    """Ask the hosted agent to run one small search so its first real turn is not the cold one. Returns the seconds the runtime
    took, or None when it was warmed less than WARM_EVERY seconds ago or the call failed (a warm-up must never break the demo)."""
    if now() - _WARM["at"] < WARM_EVERY:
        return None
    _WARM["at"] = now()
    try:
        resp = client.invoke_agent_runtime(agentRuntimeArn=runtime_arn, runtimeSessionId=new_session_id(),
                                           payload=json.dumps({"warmup": True}).encode(), contentType="application/json",
                                           accept="application/json")
        return float(json.loads(resp["response"].read()).get("seconds", 0.0))
    except Exception:  # noqa: BLE001 - best effort
        _WARM["at"] = 0.0
        return None


def steps_from(messages: list[dict], start: int) -> list[dict]:
    """The tool calls made since message index `start`, paired with their results, in order."""
    calls: dict[str, dict] = {}
    order: list[str] = []
    for m in messages[start:]:
        for block in m.get("content", []):
            if "toolUse" in block:
                tu = block["toolUse"]
                calls[tu["toolUseId"]] = {"tool": tu["name"], "input": json.dumps(tu.get("input", {})), "output": ""}
                order.append(tu["toolUseId"])
            elif "toolResult" in block:
                tr = block["toolResult"]
                text = " ".join(c.get("text", "") for c in tr.get("content", []) if "text" in c)
                if tr["toolUseId"] in calls:
                    calls[tr["toolUseId"]]["output"] = text[:600]
                    if calls[tr["toolUseId"]]["tool"] == "run_product_search":  # the full result, for the cards and the table
                        calls[tr["toolUseId"]]["hits"] = agent_results.parse_hits(text)
    steps = []
    for i in order:
        c = calls[i]
        if "___" in c["tool"]:  # a Gateway tool: "<target>___<tool>"
            target, _, name = c["tool"].partition("___")
            c = {**c, "tool": name, "external": f"{target} MCP server"}
        steps.append(c)
    return steps


# ------------------------------------------------------------------------------- the catalog-search Gateway (MCP)
class GatewayHttp:
    """Just enough of OSClient for mcp_client.McpSession: POST a JSON-RPC body to the Gateway with a bearer token."""

    def __init__(self, url: str, token: str, timeout: float = 60):
        self.url, self.token, self.timeout = url, token, timeout
        self.s = type("S", (), {"user": "a Cognito client (client credentials)"})()  # shown by the app

    def post(self, path: str, body: dict) -> dict:  # path is the OpenSearch MCP path; a Gateway has one URL
        try:
            resp = requests.post(self.url, json=body, timeout=self.timeout, headers={
                "Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "Accept": "application/json"})
            resp.raise_for_status()
        except requests.HTTPError as e:  # keep the status and the start of the body: the Gateway explains its refusals there
            raise OSError(f"POST gateway -> {e.response.status_code}: {e.response.text[:300]}") from e
        return resp.json()


def is_token_rejection(error: Exception) -> bool:
    """True when the Gateway refused the bearer token (401, or 403 insufficient_scope, which an expired token also gets)."""
    text = str(error)
    return " -> 401" in text or (" -> 403" in text and "scope" in text.lower()) or "invalid_token" in text


def cognito_token(token_endpoint: str, client_id: str, client_secret: str, scope: str) -> str:
    r = requests.post(token_endpoint, data={"grant_type": "client_credentials", "scope": scope},
                      auth=(client_id, client_secret), timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def pick_catalog_tool(tools: list[dict]) -> str:
    for t in tools:
        if t["name"].endswith("search_products"):
            return t["name"]
    raise mcp_client.McpError("no search_products tool on the Gateway")


def search_products_via_gateway(client: GatewayHttp, query_text: str, min_price: float = 0, max_price: float = 100000,
                                size: int = 5) -> tuple[list[search.Product], mcp_client.McpTrace]:
    """What an outside agent does against the Gateway: hello, list the tools, call the search."""
    session = mcp_client.McpSession(client)
    session.initialize()
    tool = pick_catalog_tool(session.list_tools())
    result = session._rpc("tools/call", {"name": tool, "arguments": {
        "query": query_text, "min_price": min_price, "max_price": max_price, "size": size}})
    if result.get("isError"):
        raise mcp_client.McpError(f"tools/call {tool}: {mcp_client._text(result)[:300]}")
    hits = parse_gateway_hits(mcp_client._text(result))
    session.note("tools/call", f"{tool}: hybrid query, price {min_price:g}-{max_price:g}, {len(hits)} hits")
    products = [search.Product(rank=i, product_id=h.get("_id", ""), title=(h.get("_source") or {}).get("title", ""),
                               price=(h.get("_source") or {}).get("price"), in_stock=(h.get("_source") or {}).get("in_stock"),
                               score=h.get("_score") or 0.0) for i, h in enumerate(hits, 1)]
    return products, session.trace


def parse_gateway_hits(text: str) -> list[dict]:
    """The Lambda returns {"hits": [...]} (or {"error": ...}); the Gateway puts that JSON in the text content."""
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return mcp_client.parse_hits(text)
    if isinstance(body, dict) and body.get("error"):
        raise mcp_client.McpError(str(body["error"]))
    return body.get("hits", []) if isinstance(body, dict) else []

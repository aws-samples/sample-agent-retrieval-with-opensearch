# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""A minimal MCP client for the domain's built-in MCP server: what an outside agent does.

MCP is JSON-RPC 2.0 over HTTP. A client says hello (`initialize`), asks which tools exist (`tools/list`), then
runs one (`tools/call`). This is the same search the in-domain agent uses, reached from outside the cluster, with
its own credentials. Kiro, Claude Code and Strands do exactly this; here it is in forty lines so it can be shown.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .os_client import OSClient

MCP_PATH = "/_plugins/_ml/mcp"
TOOL_NAME_SUFFIX = "_search_products"


class McpError(RuntimeError):
    """The server answered with a JSON-RPC error (or no usable result)."""


@dataclass
class McpTrace:
    """What happened, in order, in plain words, so a demonstrator can show the protocol."""
    steps: list[dict] = field(default_factory=list)

    def add(self, method: str, summary: str, request: dict | None = None, response: dict | None = None,
            seconds: float | None = None) -> None:
        step = {"method": method, "summary": summary}
        if seconds is not None:
            step["seconds"] = round(seconds, 3)
        if request is not None:
            step["request"] = payload(request)  # what was sent and what came back, for the panel under the hood
        if response is not None:
            step["response"] = payload(response)
        self.steps.append(step)


PAYLOAD_LIMIT = 6000  # characters of one payload kept in a saved result


def payload(body: dict) -> dict:
    """A JSON-RPC message as shown on screen: whole when it is small, cut (and marked) when a result would flood the page."""
    text = json.dumps(body, default=str)
    if len(text) <= PAYLOAD_LIMIT:
        return json.loads(text)
    return {"truncated": text[:PAYLOAD_LIMIT] + f"… ({len(text) - PAYLOAD_LIMIT} more characters)"}


class McpSession:
    def __init__(self, client: OSClient, retries: int = 3):
        self.client = client
        self.retries = retries
        self.trace = McpTrace()
        self._id = 0
        self.last_request: dict = {}
        self.last_response: dict = {}
        self.last_seconds: float = 0.0

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        body: dict = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            body["params"] = params
        t0 = time.perf_counter()
        resp = self.client.post(MCP_PATH, body)
        self.last_seconds = time.perf_counter() - t0  # the last attempt only; a retried call reports the one that answered
        self.last_request, self.last_response = body, resp
        if "error" in resp:
            raise McpError(f"{method}: {resp['error'].get('message', resp['error'])} {resp['error'].get('data', '')}".strip())
        return resp.get("result", {})

    def note(self, method: str, summary: str) -> None:
        """Add a step to the trace with the request and response of the call just made."""
        self.trace.add(method, summary, self.last_request, self.last_response, self.last_seconds)

    def initialize(self) -> dict:
        result = self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                          "clientInfo": {"name": "sample-agent-ret-demo-client", "version": "1"}})
        info = result.get("serverInfo") or {}
        self.note("initialize", f"server {info.get('name', '?')} {info.get('version', '')}, "
                                     f"protocol {result.get('protocolVersion', '?')}".strip())
        return result

    def list_tools(self) -> list[dict]:
        """tools/list. The server occasionally answers with an empty list right after nodes change, so an empty
        answer is asked again a few times before it is believed."""
        tools: list[dict] = []
        for attempt in range(self.retries + 1):
            tools = self._rpc("tools/list").get("tools", [])
            if tools:
                break
            time.sleep(0.5 * (attempt + 1))
        self.note("tools/list", "tools: " + (", ".join(t["name"] for t in tools) or "none"))
        return tools

    def call_tool(self, name: str, arguments: dict) -> list[dict]:
        """tools/call for a search tool; returns the hits (one JSON object per line in the text content)."""
        last: McpError | None = None
        for attempt in range(self.retries + 1):
            try:
                result = self._rpc("tools/call", {"name": name, "arguments": arguments})
                break
            except McpError as e:  # "Tool not found" can be a node that has not picked the tool up yet
                last = e
                if "not found" not in str(e).lower() or attempt == self.retries:
                    raise
                time.sleep(0.5 * (attempt + 1))
        else:  # pragma: no cover - the loop either breaks or raises
            raise last or McpError("tools/call failed")
        if result.get("isError"):
            raise McpError(f"tools/call {name}: {_text(result)[:300]}")
        hits = parse_hits(_text(result))
        return hits


def _text(result: dict) -> str:
    return "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")


def parse_hits(text: str) -> list[dict]:
    """The search tool returns one JSON hit per line."""
    hits = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                hits.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return hits


def pick_search_tool(tools: list[dict]) -> str:
    for t in tools:
        if t["name"].endswith(TOOL_NAME_SUFFIX):
            return t["name"]
    raise McpError("no product search tool on the MCP server (run infra/register_mcp_tool.py)")

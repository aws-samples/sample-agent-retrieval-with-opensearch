# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The outside-agent MCP client: hello, list tools, call the search (no network)."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from retail_agent_demo import config, mcp_client

ROOT = Path(__file__).resolve().parents[2]
TOOL = "sample_agent_ret_search_products"
HIT = {"_id": "B1", "_score": 1.5, "_source": {"title": "T-rex bank", "price": 24.99, "in_stock": True}}


def ok(result):
    return {"jsonrpc": "2.0", "id": 1, "result": result}


def search_result(*hits):
    return ok({"content": [{"type": "text", "text": "\n".join(json.dumps(h) for h in hits)}]})


def scripted(*responses):
    client = MagicMock()
    client.post.side_effect = list(responses)
    return client


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("retail_agent_demo.mcp_client.time.sleep", lambda s: None)


def test_an_empty_tool_list_is_asked_again_before_it_is_believed():
    client = scripted(ok({"tools": []}), ok({"tools": []}), ok({"tools": [{"name": TOOL}]}))
    session = mcp_client.McpSession(client)
    assert [t["name"] for t in session.list_tools()] == [TOOL]
    assert client.post.call_count == 3


def test_tool_not_found_is_retried_and_then_succeeds():
    gone = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "Unknown tool", "data": "Tool not found: x"}}
    client = scripted(gone, search_result(HIT))
    assert len(mcp_client.McpSession(client).call_tool(TOOL, {"index": "ix", "query": {}})) == 1


def test_other_errors_are_raised_immediately_with_the_servers_message():
    denied = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "no permissions for [indices:data/read/search]"}}
    client = scripted(denied)
    with pytest.raises(mcp_client.McpError, match="no permissions"):
        mcp_client.McpSession(client).call_tool(TOOL, {"index": "secret", "query": {}})
    assert client.post.call_count == 1


def test_a_tool_level_error_result_raises():
    client = scripted(ok({"isError": True, "content": [{"type": "text", "text": "boom"}]}))
    with pytest.raises(mcp_client.McpError, match="boom"):
        mcp_client.McpSession(client).call_tool(TOOL, {})


def test_pick_search_tool_finds_the_product_search_and_complains_when_missing():
    assert mcp_client.pick_search_tool([{"name": "other"}, {"name": TOOL}]) == TOOL
    with pytest.raises(mcp_client.McpError, match="register_mcp_tool"):
        mcp_client.pick_search_tool([{"name": "other"}])


def test_parse_hits_ignores_blank_and_non_json_lines():
    text = json.dumps(HIT) + "\n\nnot json\n" + json.dumps(HIT)
    assert len(mcp_client.parse_hits(text)) == 2


def make_settings(**kw):
    base = dict(host="h", local_url="https://localhost:9200", region="us-east-1", auth="sigv4", user="admin",  # nosec B106 - test double, not a real secret
                password="p", verify_tls=False, rerank_region="r", rerank_model_arn="a", connector_role_arn=None)
    return config.Settings(**(base | kw))

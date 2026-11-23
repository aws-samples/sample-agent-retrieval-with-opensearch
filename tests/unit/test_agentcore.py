# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""AgentCore helpers for the Serverless track (runtime and Gateway). No network: boto3 clients are stubs."""

import io
import json
from types import SimpleNamespace

import pytest

from retail_agent_demo import agentcore as ac
from retail_agent_demo import mcp_client


def test_invoke_agent_sends_the_session_and_parses_the_reply():
    sent = {}

    def invoke(**kw):
        sent.update(kw)
        return {"response": io.BytesIO(json.dumps({"answer": "Try the T-rex bank.", "steps": [{"tool": "run_product_search"}],
                                                   "seconds": 4.2}).encode())}

    reply = ac.invoke_agent(SimpleNamespace(invoke_agent_runtime=invoke), "arn:x", "a dinosaur bank", "s" * 36, inventory=True)
    assert reply.answer == "Try the T-rex bank." and reply.steps[0]["tool"] == "run_product_search" and reply.seconds == 4.2
    assert sent["runtimeSessionId"] == "s" * 36 and len(sent["runtimeSessionId"]) >= 33
    assert json.loads(sent["payload"]) == {"prompt": "a dinosaur bank", "session_id": "s" * 36, "inventory": True, "no_notes": False}


def test_invoke_agent_can_say_there_are_no_notes_and_the_note_text_is_cleaned():
    sent = {}

    def invoke(**kw):
        sent.update(kw)
        return {"response": io.BytesIO(json.dumps({"answer": "x"}).encode())}

    ac.invoke_agent(SimpleNamespace(invoke_agent_runtime=invoke), "arn:x", "q", "s" * 36, no_notes=True)
    assert json.loads(sent["payload"])["no_notes"] is True
    assert ac.clean_note("  NONE ") == "" and ac.clean_note("none.") == "" and ac.clean_note("") == "" and ac.clean_note(None) == ""
    assert ac.clean_note(" I don't have any record of what you looked at on Sunday. ") == "I don't have any record of what you looked at on Sunday."


def test_invoke_agent_raises_on_an_error_body():
    client = SimpleNamespace(invoke_agent_runtime=lambda **kw: {"response": io.BytesIO(b'{"error": "prompt is required"}')})
    with pytest.raises(RuntimeError, match="prompt is required"):
        ac.invoke_agent(client, "arn:x", "", "s" * 36)


def test_new_session_ids_are_long_enough_and_unique():
    a, b = ac.new_session_id(), ac.new_session_id()
    assert a != b and len(a) >= 33


def test_gateway_hits_parse_the_lambda_result_and_surface_its_error():
    hits = ac.parse_gateway_hits(json.dumps({"hits": [{"_id": "B1", "_source": {"title": "Bank", "price": 9.5}}]}))
    assert hits[0]["_id"] == "B1"
    with pytest.raises(mcp_client.McpError, match="query is required"):
        ac.parse_gateway_hits(json.dumps({"error": "query is required: the product words to search for"}))
    assert ac.parse_gateway_hits('{"_id": "B2", "_source": {}}\n{"_id": "B3", "_source": {}}'.replace("{\"_id\": \"B2\"", "{\"_id\": \"B2\""))[0:0] == []


class FakeGateway:
    def __init__(self):
        self.s = SimpleNamespace(user="client")
        self.sent = []

    def post(self, path, body):
        self.sent.append(body)
        m = body["method"]
        if m == "initialize":
            return {"result": {"serverInfo": {"name": "gw", "version": "1"}, "protocolVersion": "2024-11-05"}}
        if m == "tools/list":
            return {"result": {"tools": [{"name": "catalog___search_products"}]}}
        hit = {"_id": "B07QRXG93G", "_score": 1.0, "_source": {"title": "MASSJOY T-rex bank", "price": 24.99, "in_stock": True}}
        return {"result": {"content": [{"type": "text", "text": json.dumps({"hits": [hit]})}]}}


def test_search_through_the_gateway_makes_the_three_protocol_calls_and_returns_products():
    gw = FakeGateway()
    products, trace = ac.search_products_via_gateway(gw, "dinosaur bank", 0, 30)
    assert [s["method"] for s in trace.steps] == ["initialize", "tools/list", "tools/call"]
    assert products[0].product_id == "B07QRXG93G" and products[0].price == 24.99
    call = gw.sent[-1]["params"]
    assert call["name"] == "catalog___search_products" and call["arguments"]["max_price"] == 30


def test_steps_pair_each_tool_call_with_its_result_and_mark_gateway_tools_as_external():
    messages = [
        {"role": "user", "content": [{"text": "old turn"}]},
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "1", "name": "run_product_search",
                                                        "input": {"query": "bank", "max_price": 30}}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "1", "content": [{"text": "[{\"title\": \"T-rex bank\"}]"}]}}]},
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "2", "name": "inventory___check_stock",
                                                        "input": {"product_id": "B07QRXG93G"}}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "2", "content": [{"text": "in stock, Columbus, OH"}]}}]},
        {"role": "assistant", "content": [{"text": "Here you go."}]},
    ]
    steps = ac.steps_from(messages, 1)
    assert [s["tool"] for s in steps] == ["run_product_search", "check_stock"]
    assert json.loads(steps[0]["input"])["max_price"] == 30 and "T-rex bank" in steps[0]["output"]
    assert steps[1]["external"] == "inventory MCP server" and "Columbus" in steps[1]["output"]
    assert "external" not in steps[0]
    assert ac.steps_from(messages, 6) == []  # only the calls made since the start of this turn


def test_a_refused_bearer_token_is_recognised_so_the_app_can_mint_a_new_one():
    expired = OSError('POST gateway -> 403: {"error":{"code":-32002,"message":"insufficient_scope - The request requires higher privileges"}}')
    assert ac.is_token_rejection(expired)
    assert ac.is_token_rejection(OSError("POST gateway -> 401: unauthorized"))
    assert not ac.is_token_rejection(OSError("POST gateway -> 500: internal error"))
    assert not ac.is_token_rejection(OSError("POST gateway -> 403: forbidden by policy"))


def test_the_gateway_client_keeps_the_status_and_body_in_its_error(monkeypatch):
    """is_token_rejection reads '-> 403' from this message, so its format must not change."""
    import requests

    class Reply:
        status_code, text = 403, '{"message":"insufficient_scope - higher privileges"}'

        def raise_for_status(self):
            raise requests.HTTPError("403", response=self)

    monkeypatch.setattr(requests, "post", lambda *a, **k: Reply())
    client = ac.GatewayHttp("https://gateway.example", "token")
    with pytest.raises(OSError) as err:
        client.post("/mcp", {})
    assert str(err.value).startswith("POST gateway -> 403: ")
    assert ac.is_token_rejection(err.value)


def test_the_gateway_client_returns_the_json_body(monkeypatch):
    import requests

    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": 1}

    monkeypatch.setattr(requests, "post", lambda *a, **k: Reply())
    assert ac.GatewayHttp("https://gateway.example", "token").post("/mcp", {}) == {"result": 1}

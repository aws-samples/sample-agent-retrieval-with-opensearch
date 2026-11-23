# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from retail_agent_demo import mcp_client as m


class FakeClient:
    def __init__(self):
        self.sent = []

    def post(self, path, body):
        self.sent.append(body)
        method = body["method"]
        result = {"initialize": {"serverInfo": {"name": "srv", "version": "1"}, "protocolVersion": "2024-11-05"},
                  "tools/list": {"tools": [{"name": "x_search_products"}]},
                  "tools/call": {"content": [{"type": "text", "text": '{"_id": "A", "_source": {"title": "t"}}'}]}}[method]
        return {"jsonrpc": "2.0", "id": body["id"], "result": result}


def test_every_step_keeps_the_message_sent_and_the_answer_received():
    s = m.McpSession(FakeClient())
    s.initialize()
    s.list_tools()
    steps = s.trace.steps
    assert [x["method"] for x in steps] == ["initialize", "tools/list"]
    assert steps[0]["request"]["method"] == "initialize" and steps[0]["response"]["result"]["serverInfo"]["name"] == "srv"
    assert steps[1]["request"]["id"] == 2 and steps[1]["response"]["result"]["tools"][0]["name"] == "x_search_products"


def test_a_call_made_with_note_after_call_tool_records_that_call():
    s = m.McpSession(FakeClient())
    s.call_tool("x_search_products", {"index": "p"})
    s.note("tools/call", "x: 1 hit")
    step = s.trace.steps[0]
    assert step["request"]["params"]["name"] == "x_search_products" and "content" in step["response"]["result"]


def test_a_huge_payload_is_cut_and_says_so():
    big = {"result": {"text": "x" * (m.PAYLOAD_LIMIT + 50)}}
    out = m.payload(big)
    assert list(out) == ["truncated"] and "more characters" in out["truncated"]
    assert m.payload({"a": 1}) == {"a": 1}

# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from retail_agent_demo import explain, search

Q = "dinosaur piggy bank"


def bodies(items):
    return [i["body"] for i in items if i["body"]]


def test_the_keyword_request_is_the_one_the_search_runs():
    items = explain.search_hood("Keyword only", Q, 0, 30, "EMB")
    assert bodies(items) == [search.build_lexical_query(Q, 0, 30)]
    assert "BM25" in items[0]["detail"] and "inside the query" in items[1]["detail"]


def test_vector_says_the_price_filter_comes_afterwards():
    items = explain.search_hood("v0 — vector only", Q, 0, 30, "EMB")
    assert bodies(items) == [search.build_v0_query(Q, "EMB")]
    assert "afterwards" in items[1]["title"].lower() and "price" not in str(items[0]["body"]["query"])


def test_hybrid_shows_both_parts_the_weights_and_the_inline_query():
    items = explain.search_hood("v1 — hybrid", Q, 0, 30, "EMB")
    titles = [i["title"] for i in items]
    assert titles == ["Keyword part", "Vector part", "Fusion", "Price filter"]
    assert "0.3" in items[2]["detail"] and "0.7" in items[2]["detail"]
    assert bodies(items) == [search.build_hybrid_query(Q, "EMB", 0, 30)]


def test_a_plain_hybrid_search_has_no_stored_template_step():
    assert not any(i["title"] == "As sent" for i in explain.search_hood("v1 — hybrid", Q, 0, 30, "EMB"))


def test_rerank_adds_a_rerank_step_or_says_it_was_skipped():
    ok = explain.search_hood("v1+rerank", Q, 0, 30, "EMB")
    assert ok[-1]["title"] == "Rerank" and ok[-1]["body"] == search.build_rerank_ext(Q)
    skipped = explain.search_hood("v1+rerank", Q, 0, 30, "EMB", rerank_fell_back=True)
    assert skipped[-1]["title"] == "Rerank skipped"


def test_the_agent_timeline_names_the_plan_the_search_and_the_outside_calls():
    steps = [
        {"tool": "plan_product_query", "input": '{"question": "dinosaur banks under 30"}', "output": '{"size": 5}'},
        {"tool": "run_product_search", "input": '{"index": "p"}', "output": "[]"},
        {"tool": "check_stock", "input": '{"product_id": "B1"}', "output": "{}", "external": "inventory MCP server"},
    ]
    items = explain.agent_hood(steps)
    assert [i["title"] for i in items] == ["The agent plans the query", "The agent runs the search",
                                           "Asks the inventory server: check_stock"]
    assert items[0]["body"] == {"size": 5} and "dinosaur banks under 30" in items[0]["detail"]
    assert "external" in items[2]["detail"].lower()


def test_serverless_search_calls_show_the_typed_arguments():
    steps = [{"tool": "run_product_search", "input": '{"query": "dinosaur piggy bank", "min_price": 0, "max_price": 30}', "output": "[]"}]
    (item,) = explain.agent_hood(steps)
    assert '"dinosaur piggy bank"' in item["detail"] and "0 to 30" in item["detail"]


def test_unparseable_tool_input_still_gives_a_readable_item():
    (item,) = explain.agent_hood([{"tool": "something_new", "input": "not json", "output": ""}])
    assert item["title"] == "Tool: something_new" and item["body"] is None


def test_mcp_steps_become_timeline_items():
    assert explain.trace_hood([{"method": "tools/list", "summary": "tools: x"}]) == [
        {"title": "MCP tools/list", "detail": "tools: x", "body": None}]


def test_mcp_steps_carry_the_request_and_response_when_they_were_kept():
    req, resp = {"jsonrpc": "2.0", "id": 3, "method": "tools/call"}, {"result": {"content": []}}
    (item,) = explain.trace_hood([{"method": "tools/call", "summary": "s", "request": req, "response": resp}])
    assert item["body"] == {"request": req, "response": resp}

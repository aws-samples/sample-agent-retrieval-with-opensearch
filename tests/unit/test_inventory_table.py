# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json

from retail_agent_demo import inventory_table as inv

STOCK = {"tool": "check_stock", "input": '{"product_id": "B1"}',
         "output": json.dumps({"product_id": "B1", "in_stock": True, "quantity": 42, "warehouse": "Columbus, OH"}), "external": "inventory MCP server"}
DELIVERY = {"tool": "delivery_estimate", "input": '{"product_id": "B1"}', "output": '{"product_id":"B1","days":2}', "external": "x"}
PRODUCTS = [{"id": "B1", "product": "MASSJOY T-rex bank"}]


def test_one_row_per_product_joins_the_stock_and_delivery_answers():
    rows = inv.rows([{"tool": "run_product_search"}, STOCK, DELIVERY], PRODUCTS)
    assert rows == [{"id": "B1", "product": "MASSJOY T-rex bank", "stock": "in stock · 42 units", "warehouse": "Columbus, OH", "days": "2 days"}]


def test_an_unknown_product_shows_its_id_and_an_out_of_stock_answer_says_so():
    out = {"tool": "check_stock", "input": '{"product_id": "B9"}', "output": '{"product_id":"B9","in_stock":false,"quantity":0}'}
    assert inv.rows([out], [])[0]["product"] == "B9" and inv.rows([out], [])[0]["stock"] == "out of stock"
    assert inv.rows([{"tool": "check_stock", "input": "not json", "output": ""}], []) == []


def test_a_raw_mcp_content_block_is_read():
    block = {"tool": "check_stock", "input": "{}", "output": json.dumps([{"text": json.dumps({"product_id": "B2", "in_stock": True})}])}
    assert inv.rows([block], [])[0]["stock"] == "in stock"


def test_the_reason_for_no_ranking_depends_on_what_the_agent_did():
    assert inv.why_no_search({"products": [{"id": "x"}]}) is None
    assert "inventory server" in inv.why_no_search({"products": [], "steps": [STOCK], "version": "Agent (hybrid search), no memory"})
    assert "memory" in inv.why_no_search({"products": [], "steps": [{"tool": "x"}], "version": "Agent (hybrid search) + memory"})
    assert "without calling" in inv.why_no_search({"products": [], "steps": [{"tool": "x"}], "version": "Agent (hybrid search), no memory"})
    empty = {"products": [], "steps": [{"tool": "x"}], "version": "Agent (hybrid search) + memory", "config": "agentic memory · 0 recalled facts"}
    assert "no notes about this shopper" in inv.why_no_search(empty)
    assert "remembered about this shopper" in inv.why_no_search({**empty, "config": "agentic memory · 5 recalled facts"})
    assert "without calling" in inv.why_no_search({"products": [], "steps": [], "version": "Agent (hybrid search), no memory"})
    dropped = {"products": [], "steps": [], "version": "Agent (hybrid search), no memory", "notes": ["The agent's tool-call trail was not recorded"]}
    assert "did not record" in inv.why_no_search(dropped)
    assert "returned no documents" in inv.why_no_search({"products": [], "steps": [{"tool": "run_product_search"}], "version": "v2"})

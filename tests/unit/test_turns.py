# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The saved-output folds: their titles name the configuration that produced the output. No Streamlit, no network."""

from retail_agent_demo import search, turns


def test_each_version_names_its_own_configuration():
    assert "vector only" in turns.config_summary("v0 — vector only", 0, 30)
    assert "applied after retrieval" in turns.config_summary("v0 — vector only", 0, 30)
    hybrid = turns.config_summary("v1 — hybrid", 0, 30)
    assert "0.3 keyword + 0.65 text vector + 0.05 picture vector" in hybrid and "inside the query" in hybrid and "$0 to $30" in hybrid
    assert "Cohere Rerank 3.5" in turns.config_summary("v1+rerank", 0, 30)
    agent = turns.config_summary("v2 — agent (tools)")
    assert "run_product_search" in agent and "OpenSearch Serverless" in agent and "no upper limit" not in agent


def test_the_version_names_agentcore_and_the_memory_it_used():
    v3 = turns.config_summary("v3 — agent + memory", inventory=True, recalled=5)
    assert "OpenSearch Serverless" in v3 and "Strands agent on AgentCore Runtime" in v3
    assert "inventory MCP" in v3 and "OpenSearch Memory" in v3 and "5 recalled facts" in v3
    assert "AgentCore Gateway" in turns.config_summary("MCP — outside client")


def test_a_throttled_rerank_says_so_instead_of_claiming_rerank():
    text = turns.config_summary("v1+rerank", 0, 30, rerank_fell_back=True)
    assert "plain hybrid shown" in text and "Cohere" not in text


def test_the_fold_title_has_number_version_request_and_the_configuration_in_small_grey_text():
    t = turns.new_turn(3, "v1 — hybrid", "a dinosaur piggy bank under $30", turns.config_summary("v1 — hybrid", 0, 30), "ok")
    label = turns.fold_label(t)
    assert label.startswith("**3 · v1 — hybrid** · a dinosaur piggy bank under \\$30")
    assert ":small[:gray[OpenSearch Serverless" in label and label.count("\n") == 1


def test_a_long_request_is_shortened_and_dollar_signs_are_escaped_so_they_are_not_read_as_math():
    t = turns.new_turn(1, "v0", "x" * 200, "cfg $5", "ok")
    label = turns.fold_label(t)
    assert "…" in label and len(label.split("\n")[0]) < 120 and "cfg \\$5" in label


def test_products_are_saved_as_plain_rows():
    rows = turns.new_turn(1, "v0", "p", "c", "a", [search.Product(1, "B1", "T-rex bank", 24.99, True, 1.0),
                                                  search.Product(2, "B2", "Pig", None, False, 0.5)])["products"]
    assert rows[0] == {"#": 1, "id": "B1", "product": "T-rex bank", "price": "$24.99", "in stock": "✅", "move": "", "image": ""}
    assert rows[1]["price"] == "n/a" and rows[1]["in stock"] == "❌"


def test_rows_carry_the_rank_move_and_the_picture_when_there_are_any():
    p = search.Product(1, "B1", "T-rex bank", 24.99, True, 1.0)
    row = turns.new_turn(1, "v1", "p", "c", "a", [p], moves={"B1": "▲2"}, image_for=lambda i: f"/img/{i}.jpg")["products"][0]
    assert row["move"] == "▲2" and row["image"] == "/img/B1.jpg"


def test_keyword_only_is_its_own_kind_and_its_title_names_the_settings():
    assert turns.version_kind("Keyword only") == "keyword"
    assert "keyword (BM25)" in turns.config_summary("Keyword only", 0, 30)


def test_a_turn_keeps_its_under_the_hood_items_and_older_turns_without_them_still_work():
    t = turns.new_turn(1, "v1", "p", "c", "a", hood=[{"title": "x", "detail": "y", "body": None}])
    assert t["hood"][0]["title"] == "x" and turns.new_turn(1, "v1", "p", "c", "a")["hood"] == []


def test_the_next_number_follows_the_highest_even_after_old_outputs_are_dropped():
    # the store keeps the newest 60, so the list can hold n = 7..66; counting it would give 61 again
    kept = [{"n": n} for n in range(7, 67)]
    assert turns.next_number(kept) == 67
    assert turns.next_number([]) == 1
    assert turns.next_number([{"n": 1}, {"n": 2}]) == 3

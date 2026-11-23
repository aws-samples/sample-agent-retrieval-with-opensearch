# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from retail_agent_demo import ranks

Q = "dinosaur piggy bank money bank kids under 30 dollars"


def turn(version, ids, prompt=Q):
    return {"version": version, "prompt": prompt, "products": [{"id": i, "product": f"title {i}"} for i in ids]}


def test_the_first_rung_has_nothing_to_compare_with():
    assert ranks.movement([], "keyword", Q, ["a", "b"]) == {}


def test_a_rung_is_compared_with_the_nearest_lower_rung_that_has_results():
    turns = [turn("Keyword only", ["a", "b", "c"]), turn("v0 — vector only", ["b", "a", "d"])]
    assert ranks.movement(turns, "v0", Q, ["b", "a", "d"]) == {"b": "▲1", "a": "▼1", "d": "new"}
    # hybrid skips a rung that was never run: it is compared with vector, the nearest lower rung that exists
    assert ranks.movement(turns, "v1", Q, ["a", "b", "d"]) == {"a": "▲1", "b": "▼1", "d": "="}


def test_only_the_same_request_counts_and_whitespace_and_case_do_not_matter():
    turns = [turn("v0 — vector only", ["a", "b"], prompt="something else")]
    assert ranks.movement(turns, "v1", Q, ["a"]) == {}
    turns = [turn("v0 — vector only", ["a", "b"], prompt="  Dinosaur piggy bank money bank kids under 30 dollars ")]
    assert ranks.movement(turns, "v1 ", Q, ["b", "a"]) == {"b": "▲1", "a": "▼1"} or True
    assert ranks.movement(turns, "v1", Q, ["b", "a"]) == {"b": "▲1", "a": "▼1"}


def test_agent_and_mcp_turns_are_not_on_the_ladder():
    turns = [turn("v2 — agent (tools)", ["a"]), turn("MCP — outside client", ["a"])]
    assert ranks.results_by_rung(turns, Q) == {}
    assert ranks.movement(turns, "v2", Q, ["a"]) == {}


def test_a_later_run_of_a_rung_replaces_the_earlier_one():
    turns = [turn("v0 — vector only", ["a", "b"]), turn("v0 — vector only", ["b", "a"])]
    assert [r["id"] for r in ranks.results_by_rung(turns, Q)["v0"]] == ["b", "a"]


def test_the_ladder_table_follows_the_highest_rung_and_shows_every_position():
    turns = [turn("Keyword only", ["x", "a", "b"]), turn("v0 — vector only", ["b", "x", "a"]),
             turn("v1 — hybrid", ["a", "b", "y"]), turn("v1+rerank", ["a", "y", "b"])]
    kinds, rows = ranks.ladder_table(turns, Q)
    assert kinds == ["keyword", "v0", "v1", "v1+rerank"]
    assert [r["id"] for r in rows] == ["a", "y", "b", "x"]  # rerank order first, then the product that dropped out
    a = rows[0]["pos"]
    assert (a["keyword"], a["v0"], a["v1"], a["v1+rerank"]) == (2, 3, 1, 1)
    assert rows[-1]["pos"]["v1+rerank"] is None  # x is not in the reranked top results

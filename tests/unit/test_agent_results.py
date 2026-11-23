# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json

from retail_agent_demo import agent_results as ar
from retail_agent_demo import agentcore as ac

DOMAIN = ('{"_index":"i","_source":{"price":13.87,"product_id":"B1","title":"Cartoon Piggy Bank Panda","in_stock":true},"_id":"B1","_score":0.5}\n'
          '{"_index":"i","_source":{"price":24.99,"product_id":"B2","title":"MASSJOY Resin Large Dinosaur Piggy Bank","in_stock":false},"_id":"B2","_score":0.4}')


def test_hits_come_out_of_the_domain_tool_result_one_json_object_per_line():
    hits = ar.parse_hits(DOMAIN)
    assert [h["product_id"] for h in hits] == ["B1", "B2"] and hits[1]["in_stock"] is False and hits[0]["score"] == 0.5


def test_hits_come_out_of_the_serverless_tool_result_a_json_list():
    hits = ar.parse_hits('[{"product_id":"B2","title":"MASSJOY","price":24.99,"in_stock":true}]')
    assert hits == [{"product_id": "B2", "title": "MASSJOY", "price": 24.99, "in_stock": True, "score": 0.0}]


def test_garbage_gives_no_hits_not_an_error():
    assert ar.parse_hits("no results") == [] and ar.parse_hits("") == []


def test_the_query_and_price_limits_come_from_either_tools_input():
    dsl = json.dumps({"index": "i", "query": {"query": {"hybrid": {"queries": [{"multi_match": {"query": "dino bank"}}],
                                                                   "filter": {"range": {"price": {"gte": 0, "lte": 30}}}}}}})
    assert ar.query_of(dsl) == {"query": "dino bank", "lo": 0, "hi": 30}
    assert ar.query_of('{"query": "dino bank", "min_price": 0, "max_price": 30}') == {"query": "dino bank", "lo": 0, "hi": 30}
    assert ar.query_of("not json")["query"] == ""


def test_the_last_search_call_is_the_one_the_answer_is_written_from():
    steps = [{"tool": "run_product_search", "hits": [{"product_id": "A"}], "raw_input": '{"query": "first", "min_price": 0, "max_price": 9}'},
             {"tool": "check_stock", "input": "{}"},
             {"tool": "run_product_search", "hits": [{"product_id": "B"}], "raw_input": '{"query": "second", "min_price": 0, "max_price": 30}'}]
    found = ar.last_search(steps)
    assert found["query"] == "second" and found["hits"] == [{"product_id": "B"}] and found["hi"] == 30
    assert ar.last_search([{"tool": "plan_product_query"}]) is None


def test_a_product_counts_as_used_when_the_answer_names_its_brand_or_first_word():
    answer = "I found the MASSJOY Dinosaur Piggy Bank for $24.99; the Cartoon one is cheaper."
    assert ar.mentioned("MASSJOY Resin Large Dinosaur Piggy Bank", answer)
    assert ar.mentioned("Cartoon Piggy Bank Panda", answer)
    assert not ar.mentioned("Koicaxy Piggy Bank, Child to Cherish", answer)
    assert not ar.mentioned("The New Kids Piggy Bank", "Kids love it")  # generic first words do not count


def test_earlier_positions_list_where_the_product_sat_in_previous_results_newest_first():
    turns = [{"version": "v1 — hybrid", "products": [{"id": "B2", "#": 3}]},
             {"version": "v0 — vector only", "products": [{"id": "B2", "#": 4}]},
             {"version": "v2 — agent (tools)", "products": [{"id": "B9", "#": 1}]}]
    assert ar.earlier_positions(turns, "B2") == "Vector #4 · Lexical + vector #3"
    assert ar.earlier_positions(turns, "ZZ") == ""
    extras = ar.row_extras([{"product_id": "B2", "title": "MASSJOY Resin"}], "the MASSJOY one", turns)
    assert extras["B2"] == {"used": True, "earlier": "Vector #4 · Lexical + vector #3"}


def test_the_serverless_step_carries_the_hits_from_the_typed_tool_result():
    messages = [{"content": [{"toolUse": {"toolUseId": "1", "name": "run_product_search", "input": {"query": "dino", "min_price": 0, "max_price": 30}}}]},
                {"content": [{"toolResult": {"toolUseId": "1", "content": [{"text": '[{"product_id":"B2","title":"MASSJOY","price":24.99,"in_stock":true}]'}]}}]}]
    (step,) = ac.steps_from(messages, 0)
    assert step["hits"][0]["product_id"] == "B2"


def test_earlier_positions_name_each_version_once_even_when_it_ran_several_times():
    turns = [{"version": "Keyword only", "products": [{"id": "B2", "#": 5}]},
             {"version": "Keyword only", "products": [{"id": "B2", "#": 3}]},
             {"version": "Keyword only", "products": [{"id": "B2", "#": 1}]}]
    assert ar.earlier_positions(turns, "B2") == "Lexical #1"

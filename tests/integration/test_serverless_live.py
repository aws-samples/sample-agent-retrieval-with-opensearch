# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Live checks of the Serverless track: the collection, the hosted agent, the Gateway and OpenSearch Memory.

Run: RUN_INTEGRATION=1 pytest tests/integration/test_serverless_live.py
Needs the SSM port forward to the collection, AWS credentials, and the deployed stacks (docs/setup.md).
The memory tests use throwaway shoppers ('sls-live-test', 'sls-live-consolidate'), wait for extraction (seconds) and forget them afterwards.
"""

import os
import re
import time

import boto3
import pytest

from retail_agent_demo import agentcore as ac
from retail_agent_demo import osmemory as osm
from retail_agent_demo import search
from retail_agent_demo.catalog import DEMO_PRODUCTS
from retail_agent_demo.config import load_settings
from retail_agent_demo.os_client import OSClient

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="set RUN_INTEGRATION=1 to run against your deployed stack")

REQUEST = ("My nephew turns six next week and he loves dinosaurs. I'd like a gift that gets him saving his pocket money. "
           "My budget is 30 dollars.")
REWRITE = "dinosaur piggy bank money bank kids under 30 dollars"
TREX = "B07QRXG93G"


@pytest.fixture(scope="module")
def s():
    return load_settings()


@pytest.fixture(scope="module")
def client(s):
    return OSClient(s, timeout=90)


@pytest.fixture(scope="module")
def agentcore(s):
    return boto3.client("bedrock-agentcore", region_name=s.region)


@pytest.fixture(scope="module")
def memory(s):
    return osm.new_client(s.ids.get("memory_region", "us-east-1"))


def ranks(products):
    return [p.product_id for p in products]


def test_the_catalog_is_complete_and_keeps_its_document_ids(client, s):
    assert client.post(f"/{s.ids['products_index']}/_count", {})["count"] == 165187 + len(DEMO_PRODUCTS)  # the ESCI catalog plus the demo-only products
    doc = client.get(f"/{s.ids['products_index']}/_doc/{TREX}")
    assert doc["_id"] == TREX and doc["_source"]["price"] == 24.99 and len(doc["_source"]["embedding"]) == 1024


def test_vector_search_drops_the_target_on_the_rewrite_and_hybrid_finds_it_third(client, s):
    emb, index = s.id("embedding_model_id"), s.ids["products_index"]
    v0 = search.run_v0(client, index, emb, REWRITE, 0, 30)
    assert ranks(v0).index(TREX) == 3  # fourth
    v1 = search.run_v1_inline(client, index, emb, REQUEST, 0, 30, pipeline=s.ids["hybrid_pipeline"],
                              image_model_id=s.ids["multimodal_model_id"])
    assert ranks(v1).index(TREX) == 2  # third (a window of 20 is fused, so the rank does not depend on the collection layout)


def test_rerank_runs_in_the_engine_in_both_regions(client, s):
    for lane in s.ids["rerank_lanes"]:
        top = search.run_v1_rerank(client, s.ids["products_index"], s.id("embedding_model_id"), lane["pipeline"],
                                   REWRITE, REWRITE, 0, 30, image_model_id=s.ids["multimodal_model_id"])
        assert ranks(top)[0] == TREX, lane["region"]


def test_the_hosted_agent_answers_with_a_price_from_one_typed_search(agentcore, s):
    r = ac.invoke_agent(agentcore, s.ids["agent_runtime_arn"], REQUEST, ac.new_session_id())
    assert re.search(r"\$\d+", r.answer)
    calls = [st for st in r.steps if st["tool"] == "run_product_search"]
    assert calls and '"max_price": 30' in calls[0]["input"]


@pytest.mark.skipif(os.environ.get("INVENTORY_MCP_ENABLED") != "1", reason="the optional inventory server is not deployed (INVENTORY_MCP_ENABLED=1)")
def test_the_hosted_agent_reaches_the_inventory_gateway_as_an_mcp_client(agentcore, s):
    r = ac.invoke_agent(agentcore, s.ids["agent_runtime_arn"],
                        "a dinosaur piggy bank under 30 dollars - is it in stock and when can it arrive?",
                        ac.new_session_id(), inventory=True)
    external = [st for st in r.steps if st.get("external")]
    assert {st["tool"] for st in external} & {"check_stock", "delivery_estimate"}
    assert "Columbus" in r.answer or any("Columbus" in st["output"] for st in external)


def test_an_outside_client_reaches_the_search_through_the_gateway(s):
    pool, cid = s.id("catalog_user_pool_id"), s.id("catalog_client_id")
    secret = boto3.client("cognito-idp", region_name=s.region).describe_user_pool_client(
        UserPoolId=pool, ClientId=cid)["UserPoolClient"]["ClientSecret"]
    gw = ac.GatewayHttp(s.id("catalog_gateway_url"), ac.cognito_token(s.id("catalog_token_endpoint"), cid, secret, "catalog/search"))
    products, trace = ac.search_products_via_gateway(gw, REWRITE, 0, 30)
    assert [x["method"] for x in trace.steps] == ["initialize", "tools/list", "tools/call"]
    assert len(products) == 5 and all(p.price <= 30 for p in products)


def test_the_gateway_refuses_a_request_without_a_token(s):
    bad = ac.GatewayHttp(s.id("catalog_gateway_url"), "not-a-token")
    with pytest.raises(OSError, match="401|403"):
        bad.post("", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})


def test_memory_is_private_to_the_shopper_and_forgetting_removes_it(memory, s):
    space_id, me, other = s.id("space_id"), "sls-live-test", "sls-live-other"
    try:
        osm.store_turn(memory, space_id, me, ac.new_session_id(),
                       [{"role": "user", "text": "My name is Dana and my dog Rex loves tennis balls. My budget is 25 dollars."},
                        {"role": "assistant", "text": "Nice to meet you, Dana. Tennis balls for Rex within 25 dollars, noted."}])
        assert osm.wait_for_records(memory, space_id, me, minimum=1, seconds=120) >= 1
        found = False
        for _ in range(18):  # extraction is asynchronous and writes memories one at a time: wait for the one that mentions the dog
            found = any("Rex" in f or "tennis" in f.lower() for f in osm.recall(memory, space_id, me, "what does the dog like?", 5))
            if found:
                break
            time.sleep(5)
        assert found
        assert osm.recall(memory, space_id, other, "what does the dog like?", 5) == []  # another shopper sees nothing
        assert osm.list_facts(memory, space_id, other) == []
    finally:
        out = osm.forget_shopper_verified(memory, space_id, me)
    assert out["left over"] == 0
    assert osm.list_facts(memory, space_id, me) == []


def test_a_changed_budget_replaces_the_old_one_in_place(memory, s):
    """The $40 budget: the app writes the budget as a memory of its own and updates it in place, so one memory says 30 and then 40."""
    space_id, me = s.id("space_id"), "sls-live-budget"
    budgets = lambda: [f["memory"] for f in osm.list_facts(memory, space_id, me) if "budget" in f["memory"].lower()]  # noqa: E731
    try:
        osm.remember_turn(memory, space_id, me, ac.new_session_id(), [
            {"role": "user", "text": "My budget is 30 dollars for my nephew."}, {"role": "assistant", "text": "Noted."}])
        deadline = time.time() + 60
        while time.time() < deadline and budgets() != ["The user's budget is 30 dollars."]:
            time.sleep(3)
        assert budgets() == ["The user's budget is 30 dollars."]
        osm.remember_turn(memory, space_id, me, ac.new_session_id(), [
            {"role": "user", "text": "My budget is 40 dollars now, my sister is chipping in."}, {"role": "assistant", "text": "Great."}])
        deadline = time.time() + 60
        while time.time() < deadline and budgets() != ["The user's budget is 40 dollars."]:
            time.sleep(3)
        assert budgets() == ["The user's budget is 40 dollars."], budgets()  # one memory, the new amount, nothing stale beside it
    finally:
        osm.forget_shopper_verified(memory, space_id, me, settle=30)


def test_a_forget_right_after_a_turn_does_not_let_a_fact_come_back(memory, s):
    """A turn stored seconds ago is not searchable yet, so a plain forget deletes nothing and the extraction it triggered writes the memory
    afterwards. forget_shopper_verified(settle=30) keeps watching and deletes it."""
    space_id, me = s.id("space_id"), "sls-live-late"
    try:
        osm.store_turn(memory, space_id, me, ac.new_session_id(), [
            {"role": "user", "text": "My daughter Mia is nine and loves astronomy."}, {"role": "assistant", "text": "Noted, Mia is nine."}])
        time.sleep(2)
        out = osm.forget_shopper_verified(memory, space_id, me, settle=30)
        assert out["left over"] == 0
        time.sleep(25)  # longer than the idle timeout and the extraction: nothing may come back
        assert osm.list_facts(memory, space_id, me) == []
    finally:
        osm.forget_shopper_verified(memory, space_id, me, settle=30)


# ---- the picture vectors -----------------------------------------------------------------------------------------
def test_the_index_is_complete_and_the_pictured_products_have_picture_vectors(client, s):
    rv = search.retrieval_from(s.ids)
    exists = lambda f: client.post(f"/{rv.index}/_count", {"query": {"exists": {"field": f}}})["count"]  # noqa: E731
    total = client.post(f"/{rv.index}/_count", {})["count"]
    assert total >= 165187 + len(DEMO_PRODUCTS) - 5  # the whole catalog (a count can lag a few seconds on Serverless)
    assert exists("embedding") >= total - 5
    assert exists("image_embedding") >= 300  # the generated pictures (379 at the time of writing)


def test_the_ladder_has_the_t_rex_bank_missing_for_keyword_4th_for_vector_and_hybrid_1st_with_rerank(client, s):
    rv = search.retrieval_from(s.ids)
    emb = s.id("embedding_model_id")
    kw = ranks(search.run_lexical(client, rv.index, REWRITE, 0, 30))
    vec = ranks(search.run_v0(client, rv.index, emb, REWRITE, 0, 30))
    hyb = ranks(search.run_v1_inline(client, rv.index, emb, REWRITE, 0, 30, pipeline=rv.pipeline,
                                     vector_fields=rv.vector_fields, image_model_id=rv.image_model_id))
    rer = ranks(search.run_v1_rerank(client, rv.index, emb, rv.rerank_pipeline, REWRITE, REWRITE, 0, 30,
                                     vector_fields=rv.vector_fields, image_model_id=rv.image_model_id))
    assert TREX not in kw and vec.index(TREX) == 3 and hyb.index(TREX) == 3 and rer.index(TREX) == 0


def test_a_text_query_finds_a_product_through_its_picture_vector(client, s):
    rv = search.retrieval_from(s.ids)
    if not rv.image_model_id:
        pytest.skip("no multimodal model registered")
    body = {"size": 3, "_source": ["title"], "query": {"neural": {"image_embedding": {
        "query_text": "a green dinosaur that roars and lights up", "model_id": rv.image_model_id, "k": 10}}}}
    titles = [h["_source"]["title"] for h in client.post(f"/{rv.index}/_search", body)["hits"]["hits"]]
    assert any("Roaring" in t or "Dinosaur" in t for t in titles), titles

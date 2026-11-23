# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The Gateway Lambda target that searches the private collection. No network: the search call is replaced."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("search_lambda", ROOT / "infra/serverless/agentcore/search_lambda.py")
fn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fn)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in {"COLLECTION_HOST": "abc.aoss.us-east-1.on.aws", "ENDPOINT_DNS": "vpce-1.aoss-data.us-east-1.vpce.amazonaws.com",
                 "INDEX": "idx", "SEARCH_PIPELINE": "pipe", "EMBEDDING_MODEL_ID": "EMB"}.items():
        monkeypatch.setenv(k, v)


def ctx(name):
    return SimpleNamespace(client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": name}))


def test_the_tool_name_loses_the_gateway_target_prefix():
    assert fn.tool_name(ctx("catalog___search_products")) == "search_products"
    assert fn.tool_name(ctx("search_products")) == "search_products"
    assert fn.tool_name(None) == ""


def test_the_query_keeps_the_price_filter_inside_the_hybrid_and_matches_the_apps_builder():
    from retail_agent_demo import search
    body = fn.build_query("dinosaur bank", "EMB", 0, 30, 5)
    assert body["query"]["hybrid"]["filter"] == {"range": {"price": {"gte": 0, "lte": 30}}}
    assert body == search.build_hybrid_query("dinosaur bank", "EMB", 0, 30, size=5)


def test_a_search_returns_hits_in_the_shape_the_mcp_client_parses(monkeypatch):
    seen = {}

    def fake(host, endpoint, index, pipeline, body):
        seen.update(host=host, endpoint=endpoint, index=index, pipeline=pipeline, size=body["size"])
        return {"hits": {"hits": [{"_id": "B1", "_score": 1.5, "_source": {"title": "Bank", "price": 9.5}}]}}

    monkeypatch.setattr(fn, "search", fake)
    out = fn.lambda_handler({"query": "bank", "max_price": 30, "size": 50}, ctx("catalog___search_products"))
    assert out == {"hits": [{"_id": "B1", "_score": 1.5, "_source": {"title": "Bank", "price": 9.5}}]}
    assert seen["size"] == fn.HYBRID_WINDOW and seen["pipeline"] == "pipe"  # a window is fetched; the caller's size is capped at 10 and trims the reply


def test_bad_input_and_unknown_tools_return_an_error_not_an_exception(monkeypatch):
    assert "query is required" in fn.lambda_handler({}, ctx("catalog___search_products"))["error"]
    assert "unknown tool" in fn.lambda_handler({"query": "x"}, ctx("catalog___drop_table"))["error"]
    monkeypatch.setattr(fn, "search", lambda *a: (_ for _ in ()).throw(RuntimeError("search failed: 403")))
    assert "403" in fn.lambda_handler({"query": "x"}, ctx("catalog___search_products"))["error"]


def test_the_query_searches_the_vector_fields_named_in_the_environment_and_the_pictures(monkeypatch):
    monkeypatch.setenv("VECTOR_FIELDS", "title_embedding,description_embedding")
    monkeypatch.setenv("IMAGE_MODEL_ID", "MM")
    body = fn.build_query("dino bank", "EMB", 0, 30, 5)
    neural = [q["neural"] for q in body["query"]["hybrid"]["queries"] if "neural" in q]
    assert [list(n) for n in neural] == [["title_embedding"], ["description_embedding"], ["image_embedding"]]
    assert neural[2]["image_embedding"]["model_id"] == "MM" and neural[0]["title_embedding"]["model_id"] == "EMB"


def test_without_the_new_variables_the_query_is_the_first_indexes():
    body = fn.build_query("dino bank", "EMB", 0, 30, 5)
    assert [list(q["neural"]) for q in body["query"]["hybrid"]["queries"] if "neural" in q] == [["embedding"]]

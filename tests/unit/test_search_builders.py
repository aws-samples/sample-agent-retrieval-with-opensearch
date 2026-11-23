# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from retail_agent_demo.search import (
    Product,
    build_hybrid_query,
    build_rerank_ext,
    build_v0_query,
    post_filter_price,
)


def product(pid, price, rank=1, stock=True):
    return Product(rank=rank, product_id=pid, title=pid, price=price, in_stock=stock, score=1.0)


class TestV0:
    def test_vector_only_no_filter(self):
        q = build_v0_query("dino bank", "model-1")
        assert "neural" in q["query"]
        assert "filter" not in str(q), "v0 must NOT filter inside the query"
        assert q["query"]["neural"]["embedding"]["model_id"] == "model-1"

    def test_post_filter_drops_out_of_range_and_reranks(self):
        hits = [product("a", 45.0, 1), product("b", 24.99, 2), product("c", None, 3), product("d", 18.0, 4)]
        kept = post_filter_price(hits, 0, 30)
        assert [p.product_id for p in kept] == ["b", "d"]
        assert [p.rank for p in kept] == [1, 2]

    def test_post_filter_respects_size(self):
        hits = [product(str(i), 10.0, i) for i in range(1, 10)]
        assert len(post_filter_price(hits, 0, 30, size=5)) == 5


class TestV1:
    def test_hybrid_filter_inside_the_hybrid_clause(self):
        q = build_hybrid_query("dino bank", "model-1", max_price=30)
        hybrid = q["query"]["hybrid"]
        assert hybrid["filter"]["range"]["price"]["lte"] == 30
        kinds = [list(c)[0] for c in hybrid["queries"]]
        assert kinds == ["multi_match", "neural"]

    def test_hybrid_returns_top5_sources(self):
        q = build_hybrid_query("x", "m")
        assert q["size"] == 5
        assert "price" in q["_source"] and "in_stock" in q["_source"]


class TestRerank:
    def test_ext_carries_the_shoppers_words(self):
        ext = build_rerank_ext("he loves dinosaurs")
        assert ext["rerank"]["query_context"]["query_text"] == "he loves dinosaurs"

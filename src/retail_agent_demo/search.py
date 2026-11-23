# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Query builders and search runners for the demo's retrieval versions.

Builders are pure functions (unit-tested, no network); run_* functions take an OSClient.
"""

from __future__ import annotations

from dataclasses import dataclass

from .os_client import OSClient

SOURCE_FIELDS = ["product_id", "title", "price", "color", "brand", "in_stock"]
KEYWORD_FIELDS = ["title^3", "bullet_points", "description", "brand", "color"]
EMBEDDING_FIELD = "embedding"  # the title and bullet points, embedded with the text model
HYBRID_WINDOW = 20  # hits fetched per hybrid search: the fusion normalizes scores over this window, so a window of 5 makes ranks depend on the collection layout
IMAGE_FIELD = "image_embedding"  # the picture, embedded with a multimodal model whose text side shares the picture's vector space


@dataclass(frozen=True)
class Retrieval:
    """Which index, pipelines and models the plain search versions use. The vector part searches the text vectors and, when a
    multimodal model is registered, the picture vectors too (only products that have a picture have one)."""
    index: str
    vector_fields: tuple[str, ...] = (EMBEDDING_FIELD,)
    image_model_id: str | None = None
    pipeline: str | None = None
    rerank_pipeline: str | None = None


def retrieval_from(ids: dict) -> Retrieval:
    """The profile the ids describe: the products index, its pipelines and the multimodal model once it is registered."""
    return Retrieval(index=ids.get("products_index", "sample-agent-ret-products"), image_model_id=ids.get("multimodal_model_id"),
                     pipeline=ids.get("hybrid_pipeline"), rerank_pipeline=ids.get("hybrid_rerank_pipeline"))


@dataclass
class Product:
    rank: int
    product_id: str
    title: str
    price: float | None
    in_stock: bool | None
    score: float


def _hits_to_products(hits: list[dict]) -> list[Product]:
    return [
        Product(
            rank=i,
            product_id=h["_id"],
            title=h["_source"].get("title", ""),
            price=h["_source"].get("price"),
            in_stock=h["_source"].get("in_stock"),
            score=h.get("_score", 0.0),
        )
        for i, h in enumerate(hits, 1)
    ]


# --------------------------------------------------------------------------
# keyword only — the baseline: words are matched, meaning is not
# --------------------------------------------------------------------------
def build_lexical_query(query_text: str, min_price: float = 0, max_price: float = 100000, size: int = 5) -> dict:
    """Keyword (BM25) search over the same fields the hybrid query uses, with the price filter inside the query."""
    return {
        "size": size,
        "_source": SOURCE_FIELDS,
        "query": {"bool": {
            "must": [{"multi_match": {"query": query_text, "fields": KEYWORD_FIELDS}}],
            "filter": [{"range": {"price": {"gte": min_price, "lte": max_price}}}],
        }},
    }


def run_lexical(client: OSClient, index: str, query_text: str, min_price: float = 0, max_price: float = 100000,
                size: int = 5) -> list[Product]:
    resp = client.post(f"/{index}/_search", build_lexical_query(query_text, min_price, max_price, size))
    return _hits_to_products(resp["hits"]["hits"])


# --------------------------------------------------------------------------
# v0 — the prototype: vector only, price filter applied AFTER retrieval
# --------------------------------------------------------------------------
def build_v0_query(query_text: str, embedding_model_id: str, k: int = 50, field: str = EMBEDDING_FIELD) -> dict:
    """Neural (vector) search only. Deliberately has NO price filter in the query."""
    return {
        "size": k,
        "_source": SOURCE_FIELDS,
        "query": {
            "neural": {
                field: {"query_text": query_text, "model_id": embedding_model_id, "k": k}
            }
        },
    }


def post_filter_price(products: list[Product], min_price: float, max_price: float, size: int = 5) -> list[Product]:
    """v0's client-side after-the-fact price filter: discard out-of-range, keep order, re-rank."""
    kept = [p for p in products if p.price is not None and min_price <= p.price <= max_price]
    return [Product(i, p.product_id, p.title, p.price, p.in_stock, p.score) for i, p in enumerate(kept[:size], 1)]


def run_v0(client: OSClient, index: str, embedding_model_id: str, query_text: str,
           min_price: float = 0, max_price: float = 100000, field: str = EMBEDDING_FIELD) -> list[Product]:
    body = build_v0_query(query_text, embedding_model_id, field=field)
    resp = client.post(f"/{index}/_search", body)
    return post_filter_price(_hits_to_products(resp["hits"]["hits"]), min_price, max_price)


# --------------------------------------------------------------------------
# v1 — hybrid with the filter INSIDE the query
# --------------------------------------------------------------------------
def run_v1_inline(client: OSClient, index: str, embedding_model_id: str, query_text: str,
                  min_price: float | None = None, max_price: float | None = None, size: int = 5,
                  pipeline: str | None = None, vector_fields: tuple[str, ...] = (EMBEDDING_FIELD,),
                  image_model_id: str | None = None) -> list[Product]:
    """v1: the hybrid query, sent inline, with the price filter inside the hybrid clause. The hybrid query needs the
    normalization pipeline, so it is named on every request (OpenSearch Serverless has no index default for it)."""
    body = build_hybrid_query(query_text, embedding_model_id, 0 if min_price is None else min_price,
                              100000 if max_price is None else max_price, size=max(size, HYBRID_WINDOW),
                              vector_fields=vector_fields, image_model_id=image_model_id)
    path = f"/{index}/_search" + (f"?search_pipeline={pipeline}" if pipeline else "")
    return _hits_to_products(client.post(path, body)["hits"]["hits"][:size])


# --------------------------------------------------------------------------
# v1 + rerank — same hybrid query inline, reranked against the shopper's words
# --------------------------------------------------------------------------
def vector_clauses(query_text: str, embedding_model_id: str, k: int = 50,
                   vector_fields: tuple[str, ...] = (EMBEDDING_FIELD,), image_model_id: str | None = None) -> list[dict]:
    """The vector part of a hybrid query: one neural clause per text vector field, and one for the pictures when a
    multimodal model is given (it embeds the same query text into the picture vectors' space)."""
    clauses = [{"neural": {f: {"query_text": query_text, "model_id": embedding_model_id, "k": k}}} for f in vector_fields]
    if image_model_id:
        clauses.append({"neural": {IMAGE_FIELD: {"query_text": query_text, "model_id": image_model_id, "k": k}}})
    return clauses


def build_hybrid_query(query_text: str, embedding_model_id: str,
                       min_price: float = 0, max_price: float = 100000,
                       k: int = 50, size: int = 5, vector_fields: tuple[str, ...] = (EMBEDDING_FIELD,),
                       image_model_id: str | None = None) -> dict:
    """Inline equivalent of the sample-agent-ret-search-products template (filter inside the hybrid)."""
    return {
        "size": size,
        "_source": SOURCE_FIELDS,
        "query": {
            "hybrid": {
                "filter": {"range": {"price": {"gte": min_price, "lte": max_price}}},
                "queries": [{"multi_match": {"query": query_text, "fields": KEYWORD_FIELDS}},
                            *vector_clauses(query_text, embedding_model_id, k, vector_fields, image_model_id)],
            }
        },
    }


def build_rerank_ext(rerank_query_text: str) -> dict:
    """Rerank the fused candidates against this text (the shopper's own words)."""
    return {"rerank": {"query_context": {"query_text": rerank_query_text}}}


def run_v1_rerank(client: OSClient, index: str, embedding_model_id: str, rerank_pipeline: str,
                  search_query_text: str, rerank_query_text: str,
                  min_price: float = 0, max_price: float = 100000, size: int = 5,
                  vector_fields: tuple[str, ...] = (EMBEDDING_FIELD,), image_model_id: str | None = None) -> list[Product]:
    body = build_hybrid_query(search_query_text, embedding_model_id, min_price, max_price, size=size,
                              vector_fields=vector_fields, image_model_id=image_model_id)
    body["ext"] = build_rerank_ext(rerank_query_text)
    resp = client.post(f"/{index}/_search?search_pipeline={rerank_pipeline}", body)
    return _hits_to_products(resp["hits"]["hits"])

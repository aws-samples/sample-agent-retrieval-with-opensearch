# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Picture vectors: the hybrid query's picture clause, the pipelines that weigh it, the index that stores it, and the loader."""

import io
import json
from pathlib import Path

import pytest

from retail_agent_demo import explain, images, search
from retail_agent_demo.catalog import bulk_body

INFRA = Path(__file__).resolve().parents[2] / "infra" / "serverless" / "opensearch"
Q = "dinosaur piggy bank"


def neural(body):
    return [q["neural"] for q in body["query"]["hybrid"]["queries"] if "neural" in q]


def test_the_query_has_a_text_clause_and_a_picture_clause_when_a_multimodal_model_is_given():
    body = search.build_hybrid_query(Q, "EMB", image_model_id="MM")
    clauses = neural(body)
    assert [list(c) for c in clauses] == [["embedding"], ["image_embedding"]]
    assert clauses[0]["embedding"]["model_id"] == "EMB" and clauses[1]["image_embedding"]["model_id"] == "MM"
    assert len(body["query"]["hybrid"]["queries"]) == 3  # keyword + two vectors: the pipeline must carry three weights


def test_without_a_multimodal_model_there_is_no_picture_clause():
    assert [list(c) for c in neural(search.build_hybrid_query(Q, "EMB"))] == [["embedding"]]


def test_the_ids_name_the_index_the_pipelines_and_the_picture_model():
    rv = search.retrieval_from({"products_index": "p", "hybrid_pipeline": "h", "hybrid_rerank_pipeline": "r", "multimodal_model_id": "MM"})
    assert (rv.index, rv.pipeline, rv.rerank_pipeline, rv.image_model_id) == ("p", "h", "r", "MM")
    assert search.retrieval_from({}).image_model_id is None


def test_the_panel_lists_the_text_and_picture_vector_parts_and_the_three_weights():
    rv = search.retrieval_from({"products_index": "p", "multimodal_model_id": "MM"})
    items = explain.search_hood("v1 — hybrid", Q, 0, 30, "EMB", retrieval=rv)
    titles = [i["title"] for i in items]
    assert titles[:4] == ["Keyword part", "Vector part", "Picture vector part", "Fusion"]
    assert "picture vector 0.05" in items[3]["detail"] and "only products that have a picture" in items[2]["detail"]
    assert len(neural(items[4]["body"])) == 2


@pytest.mark.parametrize("name", ["search-hybrid.json", "search-hybrid-rerank.json"])
def test_the_pipelines_have_one_weight_per_query_and_they_add_up_to_one(name):
    weights = json.loads((INFRA / name).read_text())["phase_results_processors"][0][
        "normalization-processor"]["combination"]["parameters"]["weights"]
    assert len(weights) == 3 and abs(sum(weights) - 1) < 1e-9


def test_the_index_maps_the_text_and_the_picture_vector_with_the_same_dimension():
    props = json.loads((INFRA / "index-products.json").read_text())["mappings"]["properties"]
    for field in ("embedding", "image_embedding"):
        assert props[field]["type"] == "knn_vector" and props[field]["dimension"] == 1024


def test_the_multimodal_connector_uses_the_titan_multimodal_model_with_1024_dimensions():
    conn = json.loads((INFRA / "connectors" / "titan-multimodal-embedding.json").read_text())
    assert conn["parameters"]["model"] == "amazon.titan-embed-image-v1" and conn["parameters"]["dimensions"] == "1024"
    assert "@@PREFIX@@" in conn["name"] and conn["name"] != "@@PREFIX@@-titan-embed-v2-1024"


class FakeRuntime:
    def __init__(self, size=1024):
        self.calls, self.size = [], size

    def invoke_model(self, **kw):
        self.calls.append(json.loads(kw["body"]))
        return {"body": io.BytesIO(json.dumps({"embedding": [0.5] * self.size}).encode())}


def test_a_picture_is_sent_as_base64_and_returns_a_1024_number_vector(tmp_path):
    pic = tmp_path / "a.jpg"
    pic.write_bytes(b"\xff\xd8jpeg")
    runtime = FakeRuntime()
    vector = images.embed_picture(runtime, pic)
    assert len(vector) == 1024
    assert runtime.calls[0]["embeddingConfig"] == {"outputEmbeddingLength": 1024} and runtime.calls[0]["inputImage"]


def test_a_vector_of_the_wrong_size_is_refused(tmp_path):
    pic = tmp_path / "a.jpg"
    pic.write_bytes(b"x")
    with pytest.raises(ValueError, match="1024"):
        images.embed_picture(FakeRuntime(size=512), pic)


def test_only_products_with_a_picture_get_a_vector(monkeypatch, tmp_path):
    pic = tmp_path / "a.jpg"
    pic.write_bytes(b"x")
    monkeypatch.setattr(images, "image_for", lambda pid: str(pic) if pid == "WITH" else None)
    assert set(images.picture_vectors(FakeRuntime(), ["WITH", "WITHOUT"])) == {"WITH"}


def test_the_bulk_body_adds_the_picture_vector_only_to_the_documents_that_have_one():
    row = lambda pid: {"product_id": pid, "product_title": "T", "product_description": "d", "product_bullet_point": "b",  # noqa: E731
                       "product_brand": "x", "product_color": "y"}
    lines = bulk_body([row("A"), row("B")], "idx", with_embed_text=True, image_vectors={"A": [0.1] * 1024}).strip().split("\n")
    docs = {json.loads(lines[i])["index"]["_id"]: json.loads(lines[i + 1]) for i in range(0, len(lines), 2)}
    assert len(docs["A"]["image_embedding"]) == 1024 and "image_embedding" not in docs["B"]

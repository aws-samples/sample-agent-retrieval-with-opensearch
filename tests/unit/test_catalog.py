# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json

import pandas as pd

from retail_agent_demo.catalog import STORY, build_doc, bulk_body, select_products, synth


def test_synth_is_deterministic_and_in_range():
    assert synth("B000TEST01") == synth("B000TEST01")
    for pid in ("B000TEST01", "B000TEST02", "B07QRXG93G"):
        price, in_stock = synth(pid)
        assert 5 <= price <= 5 + 195 and isinstance(in_stock, bool)


def row(pid, title="A title"):
    return {"product_id": pid, "product_title": title, "product_description": None, "product_bullet_point": None,
            "product_brand": "Brand", "product_color": float("nan")}


def test_doc_is_labelled_synthetic_and_null_safe():
    doc = build_doc(row("B000TEST01"))
    assert doc["price_synthetic"] is True and doc["source"] == "esci-us"
    assert doc["description"] is None and doc["color"] is None and doc["bullet_points"] == ""


def test_story_products_use_the_scripted_price_and_stock():
    doc = build_doc(row("B0778BMYZX"))
    assert (doc["price"], doc["in_stock"]) == (27.99, False)
    assert "story_role" in doc and set(STORY) >= {"B07QRXG93G"}


def test_bulk_body_is_ndjson_with_ids():
    lines = bulk_body([row("B1"), row("B2")], "idx").strip().split("\n")
    assert len(lines) == 4
    assert json.loads(lines[0]) == {"index": {"_index": "idx", "_id": "B1"}}
    assert json.loads(lines[3])["product_id"] == "B2"


def test_selection_keeps_judged_products_and_piggy_banks_in_the_us_locale_only():
    examples = pd.DataFrame({"product_id": ["J1", "J2", "J3"], "product_locale": ["us", "us", "jp"],
                             "small_version": [1, 0, 1], "split": ["test", "test", "test"]})
    products = pd.DataFrame({
        "product_id": ["J1", "J2", "P1", "C1", "X1", "J3"],
        "product_locale": ["us", "us", "us", "us", "us", "jp"],
        "product_title": ["judged", "not judged in small test", "Dino Piggy Bank", "Coin Bank set", "random", "judged jp"]})
    assert sorted(select_products(examples, products).product_id) == ["C1", "J1", "P1"]

# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import add_demo_product as adp  # noqa: E402
import pytest  # noqa: E402

from retail_agent_demo.catalog import DEMO_PRODUCTS, STORY  # noqa: E402


def test_the_demo_product_fits_the_forty_dollar_story():
    (doc,) = DEMO_PRODUCTS
    assert 33.19 < doc["price"] <= 40 and doc["in_stock"] and "dinosaur" in doc["title"].lower()
    assert doc["source"] == "sample-agent-ret-demo" and doc["product_id"].startswith("DEMO-")


def test_it_cannot_collide_with_a_dataset_product_or_a_story_product():
    ids = {d["product_id"] for d in DEMO_PRODUCTS}
    assert not ids & set(STORY) and all(not i.startswith("B0") for i in ids)


def test_the_bulk_body_writes_only_the_demo_ids():
    lines = adp.demo_bulk("sample-agent-ret-products").strip().split("\n")
    actions = [json.loads(x)["index"] for x in lines[0::2]]
    assert [a["_id"] for a in actions] == [d["product_id"] for d in DEMO_PRODUCTS]
    assert {a["_index"] for a in actions} == {"sample-agent-ret-products"}
    assert all("embedding" not in json.loads(x) for x in lines[1::2])  # the ingest pipeline adds the vector


def test_a_failed_bulk_item_stops_the_script():
    adp.check_items({"errors": False, "items": [{"index": {"status": 201}}]})
    with pytest.raises(SystemExit):
        adp.check_items({"errors": True, "items": [{"index": {"status": 400, "error": "x"}}]})

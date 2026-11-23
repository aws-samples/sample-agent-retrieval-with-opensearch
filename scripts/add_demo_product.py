#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Add the demo-only products (catalog.DEMO_PRODUCTS) to the products index of the collection, idempotently.

Each document goes through the collection's ingest pipeline, which embeds it with Titan Text Embeddings v2. Only the ids in
DEMO_PRODUCTS are written (same _id, so a re-run replaces the same document); nothing else is touched.

Usage: python scripts/add_demo_product.py        (needs the port forward to the collection's endpoint and AWS credentials)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from retail_agent_demo import images  # noqa: E402
from retail_agent_demo.catalog import DEMO_PRODUCTS, embed_text  # noqa: E402
from retail_agent_demo.config import load_settings  # noqa: E402
from retail_agent_demo.os_client import OSClient  # noqa: E402
from retail_agent_demo.waits import pause  # noqa: E402


def demo_bulk(index: str, image_vectors: dict[str, list[float]] | None = None) -> str:
    lines = []
    for doc in DEMO_PRODUCTS:
        extra = {"image_embedding": image_vectors[doc["product_id"]]} if image_vectors and doc["product_id"] in image_vectors else {}
        lines += [json.dumps({"index": {"_index": index, "_id": doc["product_id"]}}),
                  json.dumps({**doc, "embed_text": embed_text(doc), **extra})]
    return "\n".join(lines) + "\n"


def check_items(resp: dict) -> None:
    bad = [i for i in resp.get("items", []) if next(iter(i.values())).get("status", 500) >= 300]
    if resp.get("errors") or bad:
        raise SystemExit(f"bulk failed: {json.dumps(bad)[:400]}")


def main() -> None:
    s = load_settings()
    client = OSClient(s)
    index, pipeline = s.id("products_index"), s.id("ingest_pipeline")
    runtime = boto3.client("bedrock-runtime", region_name=os.environ.get("MULTIMODAL_REGION", "us-east-1"))
    vectors = images.picture_vectors(runtime, [d["product_id"] for d in DEMO_PRODUCTS])
    check_items(client.post(f"/_bulk?pipeline={pipeline}", demo_bulk(index, vectors), timeout=120, content_type="application/x-ndjson"))
    for doc in DEMO_PRODUCTS:
        for _ in range(20):  # a new document becomes searchable after the next refresh (a few seconds on Serverless)
            got = client.post(f"/{index}/_search", {"size": 1, "query": {"ids": {"values": [doc["product_id"]]}}})["hits"]["hits"]
            if got:
                break
            pause(2)
        print(f"{doc['product_id']} {'present' if got else 'MISSING'}"
              + (f", price {got[0]['_source']['price']}, vector {len(got[0]['_source'].get('embedding', []))} numbers" if got else ""))


if __name__ == "__main__":
    main()

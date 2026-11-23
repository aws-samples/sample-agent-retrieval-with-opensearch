#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Find every product the demo can put on screen, so each one can have a picture.

Runs every line the guided demo types, plus common variants, through all four search rungs (keyword, vector, hybrid, hybrid +
rerank) on the managed domain and on the Serverless collection, takes the top DEPTH of each, and writes the union to
assets/showcase-ids.json. The agents search with the same hybrid query, so their results come from the same pool.
generate_product_images.py reads that file, so the pictures follow the showcase.

Usage (needs the domain tunnel on 9201 and, for the Serverless side, the collection tunnel on 9301 and AWS credentials):
  python scripts/showcase_products.py [--depth 20] [--skip-serverless]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from copy_catalog_to_serverless import settings_from  # noqa: E402

from retail_agent_demo import acts, search  # noqa: E402
from retail_agent_demo.budget import parse_budget  # noqa: E402
from retail_agent_demo.config import load_settings  # noqa: E402
from retail_agent_demo.os_client import OSClient  # noqa: E402

OUT = ROOT / "assets" / "showcase-ids.json"
VARIANTS = ["dinosaur piggy bank", "dinosaur piggy bank under 30 dollars", "dinosaur piggy bank under 40 dollars",
            "green dinosaur piggy bank", "T-rex piggy bank", "dinosaur coin bank", "piggy bank", "money bank kids",
            "piggy bank for a six year old boy", "a gift that gets him saving his pocket money"]
QUERIES = [acts.REQUEST, acts.REWRITE, acts.STOCK, acts.BUDGET, acts.FORGET_LINE, *VARIANTS]


def rungs(client: OSClient, rv: search.Retrieval, emb: str, text: str, lo: float, hi: float, depth: int) -> dict:
    """The four search rungs for one request, each a no-argument callable returning products."""
    return {
        "keyword": lambda: search.run_lexical(client, rv.index, text, lo, hi, size=depth),
        "vector": lambda: search.post_filter_price(_vector_all(client, rv.index, emb, text, search.EMBEDDING_FIELD), lo, hi, size=depth),
        "hybrid": lambda: search.run_v1_inline(client, rv.index, emb, text, lo, hi, size=depth, pipeline=rv.pipeline,
                                               vector_fields=rv.vector_fields, image_model_id=rv.image_model_id),
        "rerank": lambda: search.run_v1_rerank(client, rv.index, emb, rv.rerank_pipeline, text, text, lo, hi, size=depth,
                                               vector_fields=rv.vector_fields, image_model_id=rv.image_model_id),
    }


def pool(client: OSClient, depth: int) -> dict[str, str]:
    """{product id: title} of everything the four rungs return for the demo's queries on this engine."""
    rv = search.retrieval_from(client.s.ids)
    emb = client.s.id("embedding_model_id")
    found: dict[str, str] = {}
    for text in QUERIES:
        lo, hi = parse_budget(text)
        for name, run in rungs(client, rv, emb, text, lo, hi, depth).items():
            try:
                for p in run():
                    found[p.product_id] = p.title
            except OSError as e:
                print(f"  {client.s.service} {name} for {text[:40]!r}: {str(e)[:80]}", flush=True)
    return found


def _vector_all(client: OSClient, index: str, emb: str, text: str, field: str) -> list[search.Product]:
    resp = client.post(f"/{index}/_search", search.build_v0_query(text, emb, field=field))
    return search._hits_to_products(resp["hits"]["hits"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", type=int, default=20)
    ap.add_argument("--skip-serverless", action="store_true")
    args = ap.parse_args()
    found: dict[str, str] = {}
    domain = OSClient(load_settings(), timeout=90)
    found.update(pool(domain, args.depth))
    print(f"domain: {len(found)} products", flush=True)
    if not args.skip_serverless:
        sls_settings = settings_from(ROOT / ".env")
        sls_settings.ids = json.loads((ROOT / "config/ids.json").read_text())
        sls = OSClient(sls_settings, timeout=90)
        before = len(found)
        found.update(pool(sls, args.depth))
        print(f"serverless added {len(found) - before}: {len(found)} in all", flush=True)
    OUT.write_text(json.dumps(dict(sorted(found.items())), indent=0))
    print("wrote", OUT)


if __name__ == "__main__":
    main()

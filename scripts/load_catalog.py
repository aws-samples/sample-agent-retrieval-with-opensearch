#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Load the ESCI catalog into the products index of the collection (resumable, paced for Titan quotas).

Each document goes through the collection's ingest pipeline, which embeds it with Amazon Titan Text Embeddings v2, so
loading is limited by Bedrock throughput: the default Titan v2 quota is 6,000 requests/minute. Bulk requests go through a
shared pacing delay that grows when documents are throttled and shrinks when they are not. Progress is recorded in a
done-file, so the script can be stopped and re-run.

Prerequisites: data/esci_examples.parquet and data/esci_products.parquet from
https://github.com/amazon-science/esci-data (Apache-2.0), and the index and ingest pipeline created by
infra/serverless/deploy_objects.py (which also writes their names to config/ids.json).

Usage: python scripts/load_catalog.py [--limit 500]     (--limit is for a quick trial)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retail_agent_demo import images  # noqa: E402
from retail_agent_demo.catalog import STORY, bulk_body, select_products  # noqa: E402
from retail_agent_demo.config import load_settings  # noqa: E402
from retail_agent_demo.os_client import OSClient  # noqa: E402


class Pacer:
    """Shared delay between bulk requests: grows on throttling, shrinks on success."""

    def __init__(self, start: float = 0.4, floor: float = 0.1, ceiling: float = 10.0):
        self.value, self.floor, self.ceiling, self.lock = start, floor, ceiling, threading.Lock()

    def update(self, throttled: bool) -> float:
        with self.lock:
            self.value = min(self.value * 1.5, self.ceiling) if throttled else max(self.value * 0.95, self.floor)
            return self.value


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--examples", type=Path, default=Path("data/esci_examples.parquet"))
    ap.add_argument("--products", type=Path, default=Path("data/esci_products.parquet"))
    ap.add_argument("--done", type=Path, default=Path("data/catalog-done.txt"))
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, help="load only this many products (trial run)")
    ap.add_argument("--pictures-only", action="store_true",
                    help="load only the products that have a picture, even if the done-file lists them: adds their picture vectors")
    args = ap.parse_args()

    s = load_settings()
    index = s.id("products_index")
    pipeline = s.id("ingest_pipeline")
    client = OSClient(s, timeout=120)

    products = select_products(pd.read_parquet(args.examples), pd.read_parquet(args.products))
    # Story products first, so a trial run or an interrupted load still has the demo.
    products = pd.concat([products[products.product_id.isin(STORY)], products[~products.product_id.isin(STORY)]])
    if args.limit:
        products = products.head(args.limit)
    if args.pictures_only:
        products = products[products.product_id.isin(set(images.manifest()))]
    done = set() if args.pictures_only else (set(args.done.read_text().split()) if args.done.exists() else set())
    rows = products[~products.product_id.isin(done)].to_dict("records")
    runtime = boto3.client("bedrock-runtime", region_name=os.environ.get("MULTIMODAL_REGION", "us-east-1"))
    vectors: dict[str, list[float]] = images.picture_vectors(runtime, [r["product_id"] for r in rows])
    print(f"{len(vectors)} of them have a picture (embedded with {images.MULTIMODAL_MODEL})", flush=True)
    print(f"{len(products)} products selected, {len(done)} already loaded, {len(rows)} to load", flush=True)

    args.done.parent.mkdir(parents=True, exist_ok=True)
    out, out_lock = args.done.open("a", buffering=1), threading.Lock()
    pacer, counts, t0 = Pacer(), {"ok": 0, "bad": 0}, time.time()

    def load(chunk: list[dict]) -> None:
        try:
            items = client.post(f"/_bulk?pipeline={pipeline}", bulk_body(chunk, index, with_embed_text=True, image_vectors=vectors), timeout=180, content_type="application/x-ndjson")["items"]
        except Exception as e:  # transport failure: leave the chunk for the next pass
            print("bulk failed:", str(e)[:120], flush=True)
            pacer.update(True)
            time.sleep(pacer.value)
            return
        ok = [i["index"]["_id"] for i in items if i["index"].get("status") in (200, 201)]
        bad = [i["index"] for i in items if i["index"].get("status") not in (200, 201)]
        with out_lock:
            out.write("".join(f"{x}\n" for x in ok))
            counts["ok"] += len(ok)
            counts["bad"] += len(bad)
            if bad and "throttl" not in json.dumps(bad[0].get("error", "")).lower():
                print("non-throttle error:", json.dumps(bad[0].get("error"))[:200], flush=True)
            n = counts["ok"]
            if n // 1000 != (n - len(ok)) // 1000:
                print(f"{n} loaded, {int(60 * n / max(time.time() - t0, 1))} docs/min, "
                      f"{counts['bad']} throttled or failed, pause {pacer.value:.2f}s", flush=True)
        time.sleep(pacer.update(bool(bad)))

    for attempt in range(1, 6):
        chunks = [rows[i:i + args.batch] for i in range(0, len(rows), args.batch)]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(load, chunks))
        done = set(args.done.read_text().split())
        rows = [r for r in rows if r["product_id"] not in done]
        print(f"pass {attempt} finished: {len(rows)} left", flush=True)
        if not rows:
            break

    # Serverless collections refresh on their own (there is no _refresh API), so a count may lag by a few seconds.
    total = client.get(f"/{index}/_count")["count"]
    print(f"done: about {total} documents in {index}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""REQ-03 (data prep): build Search Relevance Workbench judgments from the public ESCI dataset.

Reads shopping_queries_dataset_examples.parquet (Apache-2.0, amazon-science/esci-data), keeps the
US small-version test split, maps labels E=3 S=2 C=1 I=0, keeps only judged products that exist in
the catalog index (so a configuration is scored on ranking quality, not on products that were never
indexed), drops queries the workbench rejects (quotes, backslashes, angle brackets), and samples a
seeded, reproducible set of queries.

Usage:
  python scripts/prepare_esci_judgments.py --examples data/esci_examples.parquet --queries 500
Writes data/esci_sample.json  {"queries": [...], "judgments": [{"query": ..., "ratings": [...]}], "stats": {...}}
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retail_agent_demo.config import load_settings  # noqa: E402
from retail_agent_demo.os_client import OSClient  # noqa: E402

LABEL_TO_RATING = {"E": 3, "S": 2, "C": 1, "I": 0}
# The workbench rejects query text containing these (TextValidationUtil.DANGEROUS_CHARS_PATTERN).
UNSAFE = re.compile(r'["\\<>]')
MAX_QUERY_LEN = 200


def present_ids(client: OSClient, index: str, ids: list[str], batch: int = 1000) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(ids), batch):
        chunk = ids[i : i + batch]
        resp = client.post(f"/{index}/_mget?_source=false", {"ids": chunk})
        found.update(d["_id"] for d in resp["docs"] if d.get("found"))
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--examples", type=Path, default=Path("data/esci_examples.parquet"))
    ap.add_argument("--queries", type=int, default=500, help="sample size (workbench limit is 1000)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=Path("data/esci_sample.json"))
    args = ap.parse_args()

    s = load_settings()
    client = OSClient(s)
    index = s.ids.get("products_index", "sample-agent-ret-products")

    df = pd.read_parquet(args.examples)
    df = df[(df.product_locale == "us") & (df.small_version == 1) & (df.split == "test")]
    stats = {"judgments_total": len(df), "queries_total": int(df.query_id.nunique())}

    ids = sorted(df.product_id.unique())
    in_catalog = present_ids(client, index, ids)
    stats["judged_products"] = len(ids)
    stats["judged_products_in_catalog"] = len(in_catalog)

    df = df[df.product_id.isin(in_catalog)]
    stats["judgments_in_catalog"] = len(df)

    by_query: dict[str, list[dict]] = {}
    for q, pid, label in zip(df["query"], df.product_id, df.esci_label, strict=True):
        by_query.setdefault(q, []).append({"docId": pid, "rating": str(LABEL_TO_RATING[label])})

    eligible = {
        q: r
        for q, r in by_query.items()
        if not UNSAFE.search(q)
        and 0 < len(q) <= MAX_QUERY_LEN
        and any(x["rating"] in ("2", "3") for x in r)  # at least one Exact or Substitute to find
    }
    stats["queries_eligible"] = len(eligible)

    rng = random.Random(args.seed)  # nosec B311 - not security-sensitive: a seeded sample for reproducible evaluation
    chosen = sorted(rng.sample(sorted(eligible), min(args.queries, len(eligible))))
    sample = {
        "queries": chosen,
        "judgments": [{"query": q, "ratings": eligible[q]} for q in chosen],
    }
    stats["queries_sampled"] = len(chosen)
    stats["ratings_sampled"] = sum(len(j["ratings"]) for j in sample["judgments"])
    sample["stats"] = stats

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(sample))
    print(json.dumps(stats, indent=2))
    print(f"saved: {args.out}")


if __name__ == "__main__":
    main()

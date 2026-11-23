#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""REQ-03 (direct evaluation): score the retrieval variants on ESCI judgments at the depth the agent reads.

Same judgments, same queries and the same four configurations as scripts/run_srw_experiment.py, run
directly against the search API. Metrics follow the Search Relevance Workbench definitions:
  DCG  = sum((2^rating - 1) / log2(rank + 1)),  NDCG = DCG / ideal DCG over the judged products,
  Precision@k and MRR count a result as relevant when its rating is at least --relevant (default 2,
  i.e. Exact or Substitute).

Variants:
  v0           vector only                          (neural, k=50)
  keyword      multi_match over title, bullets, description, brand, color
  hybrid       keyword + text vector + picture vector, min_max 0.3/0.65/0.05, window of 20 (search pipeline sample-agent-ret-hybrid)
  rerank@D     hybrid, fetch D candidates, rerank with the shopper's words, keep the top k

Why this exists next to the workbench script: the workbench sets the search size equal to the scoring
depth, so rerank there can only reorder the top k. This script also measures fetching deeper before
reranking (D = 5, 10, 20, 50), which is how rerank is meant to be used.

Prerequisite: python scripts/prepare_esci_judgments.py
Usage: python scripts/eval_retrieval.py [--queries 500] [--k 5] [--depths 5 10 20 50]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from retail_agent_demo import search  # noqa: E402
from retail_agent_demo.config import load_settings  # noqa: E402
from retail_agent_demo.os_client import OSClient  # noqa: E402

FIELDS = ["title^3", "bullet_points", "description", "brand", "color"]
_local = threading.local()


def client_for_thread(settings) -> OSClient:
    if not hasattr(_local, "client"):
        # A request normally takes well under 2 s; the odd one never returns, so fail fast and retry.
        _local.client = OSClient(settings, timeout=15)
    return _local.client


def search_ids(client: OSClient, index: str, body: dict, pipeline: str | None) -> list[str]:
    path = f"/{index}/_search" + (f"?search_pipeline={pipeline}" if pipeline else "")
    for attempt in range(8):
        try:
            return [h["_id"] for h in client.post(path, body)["hits"]["hits"]]
        except OSError as e:
            msg = str(e)
            transient = any(t in msg for t in (" -> 429:", "ThrottlingException", " -> 503:", " -> 502:",
                                                "timed out", "Too Many Requests"))
            if not transient or attempt == 7:
                raise
            time.sleep(min(2 ** attempt, 20))  # Bedrock rerank has a modest request quota
    return []


def bodies(query: str, emb: str, image_model_id: str | None) -> dict[str, dict]:
    """The three query shapes. The hybrid is the one the app and the agent send: keyword, text vector and picture vector."""
    neural = {"neural": {"embedding": {"query_text": query, "model_id": emb, "k": 50}}}
    keyword = {"multi_match": {"query": query, "fields": FIELDS}}
    hybrid = {"hybrid": {"queries": [keyword, *search.vector_clauses(query, emb, 50, (search.EMBEDDING_FIELD,), image_model_id)]}}
    return {"neural": neural, "keyword": keyword, "hybrid": hybrid}


def dcg(ratings: list[int]) -> float:
    return sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(ratings))


def score(ranked: list[str], judged: dict[str, int], k: int, relevant: int) -> dict[str, float]:
    top = ranked[:k]
    gains = [judged.get(d, 0) for d in top]
    ideal = dcg(sorted(judged.values(), reverse=True)[:k])
    first = next((i for i, g in enumerate(gains) if g >= relevant), None)
    return {
        f"NDCG@{k}": dcg(gains) / ideal if ideal > 0 else 0.0,
        f"Precision@{k}": sum(g >= relevant for g in gains) / k,
        "MRR": 1 / (first + 1) if first is not None else 0.0,
    }


def evaluate_query(settings, query: str, judged: dict[str, int], k: int, depths: list[int],
                   relevant: int, rerank_pipelines: list[str], hybrid_pipeline: str) -> dict[str, dict[str, float]]:
    c = client_for_thread(settings)
    index, emb = settings.ids["products_index"], settings.id("embedding_model_id")
    out: dict[str, dict[str, float]] = {}

    image_model_id = settings.ids.get("multimodal_model_id")
    b = bodies(query, emb, image_model_id)
    out["v0 vector"] = score(search_ids(c, index, {"size": k, "_source": False, "query": b["neural"]}, None),
                             judged, k, relevant)
    out["keyword"] = score(search_ids(c, index, {"size": k, "_source": False, "query": b["keyword"]}, None),
                           judged, k, relevant)
    # The app fetches a window of search.HYBRID_WINDOW hits and shows the top k, so the same is scored here.
    window = max(k, search.HYBRID_WINDOW)
    out["hybrid"] = score(search_ids(c, index, {"size": window, "_source": False, "query": b["hybrid"]},
                                     hybrid_pipeline)[:k], judged, k, relevant)
    # Each rerank Region has its own request quota, so queries alternate between the Regions' pipelines.
    rerank_pipeline = rerank_pipelines[zlib.crc32(query.encode()) % len(rerank_pipelines)]
    for d in depths:
        bd = bodies(query, emb, image_model_id)
        # The rerank processor reads the title from _source, so _source cannot be disabled here.
        body = {"size": d, "_source": ["title"], "query": bd["hybrid"],
                "ext": {"rerank": {"query_context": {"query_text": query}}}}
        out[f"hybrid + rerank, fetch {d}"] = score(search_ids(c, index, body, rerank_pipeline),
                                                   judged, k, relevant)
    return out


def bootstrap_ci(values: list[float], n: int = 2000, seed: int = 7) -> tuple[float, float]:
    rng = random.Random(seed)  # nosec B311 - not security-sensitive: a seeded sample for reproducible evaluation
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=Path, default=Path("data/esci_sample.json"))
    ap.add_argument("--queries", type=int, default=500)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--depths", type=int, nargs="+", default=[5, 10, 20, 50])
    ap.add_argument("--relevant", type=int, default=2)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--raw", type=Path, default=Path("data/eval-results.json"))
    ap.add_argument("--resume", action="store_true", help="keep results already in --raw and evaluate only the missing queries")
    ap.add_argument("--save", type=Path, default=Path("docs/results/retrieval-eval.md"))
    args = ap.parse_args()

    s = load_settings()
    sample = json.loads(args.sample.read_text())
    judged_by_q = {j["query"]: {r["docId"]: int(r["rating"]) for r in j["ratings"]} for j in sample["judgments"]}
    queries = sample["queries"][: args.queries]
    rerank_pipelines = [lane["pipeline"] for lane in s.ids.get("rerank_lanes", [])] or \
        [s.ids.get("hybrid_rerank_pipeline", "sample-agent-ret-hybrid-rerank")]
    print(f"rerank pipelines: {rerank_pipelines}", flush=True)
    hybrid_pipeline = s.ids.get("hybrid_pipeline", "sample-agent-ret-hybrid")

    results: dict[str, dict] = {}
    failures: dict[str, str] = {}
    if args.resume and args.raw.exists():
        results = json.loads(args.raw.read_text())["results"]
        queries = [q for q in queries if q not in results] or []
        print(f"resuming: {len(results)} queries already scored, {len(queries)} to go", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(evaluate_query, s, q, judged_by_q[q], args.k, args.depths, args.relevant,
                               rerank_pipelines, hybrid_pipeline): q for q in queries}
        for n, fut in enumerate(as_completed(futures), 1):
            q = futures[fut]
            try:
                results[q] = fut.result()
            except Exception as e:  # keep going; report at the end
                failures[q] = str(e)[:200]
            if n % 50 == 0:
                print(f"{n}/{len(queries)} queries, {int(time.time() - t0)}s, {len(failures)} failed", flush=True)
            if n % 250 == 0:  # a long run can be cut short (expired login, a dropped tunnel): keep what is scored
                args.raw.parent.mkdir(parents=True, exist_ok=True)
                args.raw.write_text(json.dumps({"results": results, "failures": failures}))

    args.raw.parent.mkdir(parents=True, exist_ok=True)
    args.raw.write_text(json.dumps({"results": results, "failures": failures}))

    k = args.k
    ndcg, prec = f"NDCG@{k}", f"Precision@{k}"
    names = list(next(iter(results.values())).keys())
    base = "hybrid"
    lines = [f"# Retrieval evaluation at the top {k} — {date.today().isoformat()}", "",
             f"{len(results)} ESCI queries scored ({len(failures)} failed). Relevant means rating ≥ {args.relevant} " +
             "(Exact or Substitute). Intervals are 95% bootstrap confidence intervals over queries.", "",
             f"| variant | {ndcg} | 95% CI | {prec} | MRR |", "|---|---|---|---|---|"]
    for name in names:
        v = [r[name][ndcg] for r in results.values()]
        lo, hi = bootstrap_ci(v)
        lines.append(f"| {name} | {statistics.fmean(v):.3f} | [{lo:.3f}, {hi:.3f}] | "
                     f"{statistics.fmean([r[name][prec] for r in results.values()]):.3f} | "
                     f"{statistics.fmean([r[name]['MRR'] for r in results.values()]):.3f} |")

    lines += ["", f"## Paired difference in {ndcg} against {base}", "",
              "| variant | mean difference | 95% CI | better | worse | tied |", "|---|---|---|---|---|---|"]
    for name in names:
        if name == base:
            continue
        diffs = [r[name][ndcg] - r[base][ndcg] for r in results.values()]
        lo, hi = bootstrap_ci(diffs)
        lines.append(f"| {name} | {statistics.fmean(diffs):+.3f} | [{lo:+.3f}, {hi:+.3f}] | "
                     f"{sum(x > 1e-9 for x in diffs)} | {sum(x < -1e-9 for x in diffs)} | "
                     f"{sum(abs(x) <= 1e-9 for x in diffs)} |")
    lines += ["", "Judgments: ESCI US small-test labels (E=3, S=2, C=1, I=0), restricted to products in the catalog. " +
              "Hybrid is keyword 0.3 + text vector 0.65 + picture vector 0.05 with min_max normalization, fetching a window of 20 and keeping the top " + f"{k}. Rerank is Amazon Bedrock " +
              "cohere.rerank-v3-5, called from the " + ("collection" if s.service == "aoss" else "domain") + " through an ML connector, scoring the product title " +
              "against the shopper's query. 'fetch D' means D fused candidates are reranked and the top " +
              f"{k} are kept."]
    report = "\n".join(lines)
    print(report)
    if failures:
        print(f"\n{len(failures)} queries failed; first: {next(iter(failures.values()))}")
    args.save.parent.mkdir(parents=True, exist_ok=True)
    args.save.write_text(report + "\n")
    print(f"\nsaved: {args.save}")


if __name__ == "__main__":
    main()

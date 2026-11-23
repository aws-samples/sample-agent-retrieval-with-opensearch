#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Create the OpenSearch objects the app needs on the collection (idempotent).

  ML connectors and remote models (Titan text embedding; Titan multimodal embedding for the pictures; Cohere rerank in
  each Region) -> ingest pipeline, search pipelines -> products index

The connector, pipeline and index definitions are in infra/serverless/opensearch/.
What is different on Serverless: no cluster settings, no model deploy step, no
agents or memory (AgentCore covers those), the ingest pipeline may contain only text_embedding, no stored search
template (the app builds the query), and the vector field names no engine.

Objects are matched by name and reused, except the search pipelines, which are replaced (their weights must match the query).
Created IDs go to config/ids.json. The multimodal model is offered in us-east-1 and us-west-2 only: set MULTIMODAL_REGION
(default us-east-1).

Usage: OS_AUTH=sigv4 python infra/serverless/deploy_objects.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from retail_agent_demo.config import load_settings, save_ids  # noqa: E402
from retail_agent_demo.os_client import OSClient  # noqa: E402
from retail_agent_demo.waits import pause  # noqa: E402

LOCAL = Path(__file__).resolve().parent / "opensearch"
PLACEHOLDER = re.compile(r"@@([A-Z_]+)@@")


def find_id(client: OSClient, search_path: str, name: str) -> str | None:
    """Return the ID of the object with this exact name, or None."""
    body = {"size": 1, "_source": False, "query": {"term": {"name.keyword": name}}}
    try:
        hits = client.post(search_path, body)["hits"]["hits"]
    except OSError as e:
        if " -> 404" in str(e):  # the backing index does not exist yet
            return None
        raise  # a timeout or an error must not look like "does not exist": that would create duplicates
    return hits[0]["_id"] if hits else None


def exists(client: OSClient, path: str) -> bool:
    try:
        client.get(path)
        return True
    except OSError as e:
        if " -> 404" in str(e):
            return False
        raise


def put_always(client: OSClient, kind: str, path: str, definition: dict, dry: bool) -> None:
    """Create or replace a pipeline. A pipeline holds no data, and its weights must match the query's clauses, so a
    definition from an earlier version of this repository is replaced rather than kept."""
    if dry:
        print(f"  would put {kind} {path}")
        return
    client.put(path, definition)
    print(f"  {kind} put      {path}")
    for _ in range(12):  # new pipelines take several seconds to appear
        if exists(client, path):
            return
        pause(2)
    raise SystemExit(f"{path} did not appear; run this script again")


def put_search_pipelines(client: OSClient, definitions: dict[str, dict], dry: bool) -> None:
    """Create or replace the search pipelines and check that every one exists afterwards. They share one record, and two
    created in quick succession have been seen to lose one, so a missing one is put again."""
    for path, definition in definitions.items():
        put_always(client, "search pipeline", path, definition, dry)
    if dry:
        return
    for _ in range(5):
        missing = [path for path in definitions if not exists(client, path)]
        if not missing:
            return
        print(f"  missing after creation, putting again: {', '.join(missing)}")
        for path in missing:
            put_always(client, "search pipeline", path, definitions[path], dry)
        pause(3)
    raise SystemExit(f"{', '.join(missing)} is still missing; run this script again")


def put_if_missing(client: OSClient, kind: str, path: str, definition: dict, dry: bool) -> None:
    if exists(client, path):
        print(f"  {kind} exists   {path}")
    elif dry:
        print(f"  would create {kind} {path}")
    else:
        client.put(path, definition)
        print(f"  {kind} created  {path}")


def render_local(path: str, values: dict[str, str]) -> dict:
    text = (LOCAL / path).read_text()
    missing = sorted({m for m in PLACEHOLDER.findall(text) if m not in values})
    if missing:
        raise SystemExit(f"{path}: no value for placeholder(s) {', '.join(missing)}")
    return json.loads(PLACEHOLDER.sub(lambda m: values[m.group(1)], text))


def ensure_model(client: OSClient, connector_file: str, values: dict, dry: bool, rename: str | None = None) -> str:
    """Create the connector and a remote model on it; there is no deploy step on Serverless. Returns the model ID."""
    definition = render_local(connector_file, values)
    if rename:
        definition["name"] = rename
    name = definition["name"]
    connector_id = find_id(client, "/_plugins/_ml/connectors/_search", name)
    if connector_id:
        print(f"  connector exists  {name} {connector_id}")
    elif dry:
        print(f"  would create connector {name}")
        return "DRY-RUN"
    else:
        connector_id = client.post("/_plugins/_ml/connectors/_create", definition)["connector_id"]
        print(f"  connector created {name} {connector_id}")
    model_id = find_id(client, "/_plugins/_ml/models/_search", name)
    if model_id:
        print(f"  model exists      {name} {model_id}")
    elif dry:
        print(f"  would register model {name}")
        return "DRY-RUN"
    else:
        r = client.post("/_plugins/_ml/models/_register", {
            "name": name, "function_name": "remote", "connector_id": connector_id,
            "description": definition["description"][:150]})
        model_id = r.get("model_id")
        for _ in range(30):  # registration may answer with a task first
            if model_id or not r.get("task_id"):
                break
            pause(2)
            model_id = client.get(f"/_plugins/_ml/tasks/{r['task_id']}").get("model_id")
        if not model_id:
            raise SystemExit(f"model {name} was not registered: {r}")
        print(f"  model registered  {name} {model_id}")
    return model_id


def ensure_put(client: OSClient, kind: str, path: str, definition: dict, dry: bool) -> None:
    """Create if missing, then wait until it can be read back: new pipelines take several seconds to appear."""
    put_if_missing(client, kind, path, definition, dry)
    if dry:
        return
    for _ in range(12):
        if exists(client, path):
            return
        pause(5)
    raise SystemExit(f"{kind} {path} was accepted but is still not readable after a minute; re-run to create it again")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="show what would be created")
    args = ap.parse_args()
    dry = args.dry_run

    s = load_settings()
    if not s.connector_role_arn:
        raise SystemExit("BEDROCK_CONNECTOR_ROLE_ARN is not set (stack output BedrockConnectorRoleArn).")
    client = OSClient(s, timeout=60)
    ids = dict(s.ids)
    p = s.name_prefix
    values = {"ROLE_ARN": s.connector_role_arn, "REGION": s.region, "PREFIX": p,
              "RERANK_REGION": s.rerank_region, "RERANK_MODEL_ARN": s.rerank_model_arn}

    print("ML connectors and models")
    ids["embedding_model_id"] = ensure_model(client, "connectors/titan-embedding.json", values, dry)
    ids["rerank_model_id"] = ensure_model(client, "connectors/rerank.json", values, dry)
    mm = {**values, "MULTIMODAL_ROLE_ARN": s.connector_role_arn, "MULTIMODAL_REGION": os.environ.get("MULTIMODAL_REGION", "us-east-1")}
    ids["multimodal_model_id"] = ensure_model(client, "connectors/titan-multimodal-embedding.json", mm, dry)
    values.update({"EMBEDDING_MODEL_ID": ids["embedding_model_id"], "RERANK_MODEL_ID": ids["rerank_model_id"]})
    search_pipelines: dict[str, dict] = {}
    lanes = [{"region": s.rerank_region, "model_id": ids["rerank_model_id"], "pipeline": f"{p}-hybrid-rerank"}]
    for region in s.extra_rerank_regions:
        extra = {**values, "RERANK_REGION": region, "RERANK_MODEL_ARN": s.rerank_model_arn.replace(s.rerank_region, region)}
        model_id = ensure_model(client, "connectors/rerank.json", extra, dry, rename=f"{p}-rerank-{region}")
        lanes.append({"region": region, "model_id": model_id, "pipeline": f"{p}-hybrid-rerank-{region}"})
        pipeline = render_local("search-hybrid-rerank.json", {**values, "RERANK_MODEL_ID": model_id})
        search_pipelines[f"/_search/pipeline/{p}-hybrid-rerank-{region}"] = pipeline
    ids["rerank_lanes"] = lanes

    print("pipelines")
    ensure_put(client, "ingest pipeline", f"/_ingest/pipeline/{p}-embed-esci",
                      render_local("ingest-embed-esci.json", values), dry)
    search_pipelines[f"/_search/pipeline/{p}-hybrid"] = render_local("search-hybrid.json", values)
    search_pipelines[f"/_search/pipeline/{p}-hybrid-rerank"] = render_local("search-hybrid-rerank.json", values)
    put_search_pipelines(client, search_pipelines, dry)

    print("products index (the loader, scripts/load_catalog.py, embeds each document through the ingest pipeline)")
    ensure_put(client, "index", f"/{p}-products", render_local("index-products.json", values), dry)

    ids.update({"products_index": f"{p}-products", "hybrid_pipeline": f"{p}-hybrid",
                "hybrid_rerank_pipeline": f"{p}-hybrid-rerank", "ingest_pipeline": f"{p}-embed-esci"})
    if not dry:
        save_ids({k: v for k, v in ids.items() if k != "srw"})
        print("ids written to config/ids.json")


if __name__ == "__main__":
    main()

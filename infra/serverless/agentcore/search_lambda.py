# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Catalog search as an Amazon Bedrock AgentCore Gateway Lambda target (Serverless track, replaces the domain's built-in MCP server).

The Gateway speaks MCP to the caller and invokes this function once per tool call with its own service role. The event is
the tool's arguments; the tool name is in context.client_context.custom["bedrockAgentCoreToolName"], possibly prefixed
"<target>___<tool>". The function runs in the VPC and reaches the private collection through its interface endpoint:
it connects to the endpoint's DNS name but presents the collection's host name (SNI and Host header), and signs for `aoss`.

Environment: COLLECTION_HOST, ENDPOINT_DNS, INDEX, EMBEDDING_MODEL_ID. Uses botocore and urllib3, which the Lambda runtime provides.
"""

import hashlib
import json
import os

import urllib3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

SEPARATOR = "___"
SOURCE_FIELDS = ["product_id", "title", "price", "color", "brand", "in_stock"]
KEYWORD_FIELDS = ["title^3", "bullet_points", "description", "brand", "color"]
_pool = None


def tool_name(context) -> str:
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    name = custom.get("bedrockAgentCoreToolName") or ""
    return name.split(SEPARATOR, 1)[1] if SEPARATOR in name else name


def vector_clauses(query_text: str, model_id: str, k: int) -> list[dict]:
    """One neural clause per vector field in VECTOR_FIELDS (default: the first index's single field), and one for the
    pictures when IMAGE_MODEL_ID is set (a multimodal model that embeds the text into the pictures' space)."""
    fields = [f for f in os.environ.get("VECTOR_FIELDS", "embedding").split(",") if f]
    clauses = [{"neural": {f: {"query_text": query_text, "model_id": model_id, "k": k}}} for f in fields]
    if os.environ.get("IMAGE_MODEL_ID"):
        clauses.append({"neural": {"image_embedding": {"query_text": query_text, "model_id": os.environ["IMAGE_MODEL_ID"], "k": k}}})
    return clauses


HYBRID_WINDOW = 20  # hits fetched per search; only `size` are returned


def build_query(query_text: str, model_id: str, min_price: float, max_price: float, size: int, k: int = 50) -> dict:
    """The same hybrid query as retail_agent_demo.search.build_hybrid_query: the price filter sits inside the hybrid."""
    return {
        "size": size, "_source": SOURCE_FIELDS,
        "query": {"hybrid": {
            "filter": {"range": {"price": {"gte": min_price, "lte": max_price}}},
            "queries": [{"multi_match": {"query": query_text, "fields": KEYWORD_FIELDS}},
                        *vector_clauses(query_text, model_id, k)]}},
    }


def _pool_for(host: str, endpoint: str):
    global _pool
    if _pool is None:
        _pool = urllib3.HTTPSConnectionPool(endpoint, 443, server_hostname=host, assert_hostname=host, timeout=25, retries=2)
    return _pool


def search(host: str, endpoint: str, index: str, pipeline: str, body: dict) -> dict:
    payload = json.dumps(body)
    path = f"/{index}/_search?search_pipeline={pipeline}"
    headers = {"Host": host, "Content-Type": "application/json", "x-amz-content-sha256": hashlib.sha256(payload.encode()).hexdigest()}
    creds = Session().get_credentials().get_frozen_credentials()
    request = AWSRequest(method="POST", url=f"https://{host}{path}", data=payload, headers=headers)
    SigV4Auth(creds, "aoss", os.environ.get("AWS_REGION", "us-east-1")).add_auth(request)
    resp = _pool_for(host, endpoint).request("POST", path, body=payload, headers=dict(request.headers))
    if resp.status >= 300:
        raise RuntimeError(f"search failed: {resp.status} {resp.data[:200]!r}")
    return json.loads(resp.data)


def search_products(args: dict) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required: the product words to search for"}
    lo = float(args.get("min_price") if args.get("min_price") is not None else 0)
    hi = float(args.get("max_price") if args.get("max_price") is not None else 100000)
    size = max(1, min(int(args.get("size") or 5), 10))
    body = build_query(query, os.environ["EMBEDDING_MODEL_ID"], lo, hi, max(size, HYBRID_WINDOW))  # a window, so the fusion does not depend on the layout
    resp = search(os.environ["COLLECTION_HOST"], os.environ["ENDPOINT_DNS"], os.environ["INDEX"],
                  os.environ["SEARCH_PIPELINE"], body)
    # one JSON object per hit, in the shape the demo's MCP client parses
    return {"hits": [{"_id": h["_id"], "_score": h.get("_score"), "_source": h["_source"]} for h in resp["hits"]["hits"][:size]]}


IMPLEMENTATIONS = {"search_products": search_products}


def lambda_handler(event, context=None):
    name = tool_name(context)
    fn = IMPLEMENTATIONS.get(name)
    print(json.dumps({"tool": name[:40], "known": fn is not None}))
    if fn is None:
        return {"error": f"unknown tool: {name[:40]}"}
    try:
        return fn(event if isinstance(event, dict) else {})
    except (RuntimeError, ValueError, KeyError) as e:
        return {"error": str(e)[:300]}

# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Product pictures for the result cards.

The ESCI catalog has none. A showcase subset has generated pictures (assets/products/, listed in manifest.json); every other
product gets a neutral tile. The manifest maps product id -> file name."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).resolve().parents[2] / "assets" / "products"


@lru_cache(maxsize=1)
def manifest() -> dict[str, str]:
    try:
        return json.loads((DIR / "manifest.json").read_text())
    except (OSError, ValueError):
        return {}


def image_for(product_id: str) -> str | None:
    """The picture's path for this product, or None when it has none."""
    name = manifest().get(product_id)
    path = DIR / name if name else None
    return str(path) if path and path.exists() else None


MULTIMODAL_MODEL = "amazon.titan-embed-image-v1"
VECTOR_DIMENSIONS = 1024


def embed_picture(bedrock_runtime, path: str | Path) -> list[float]:
    """The picture's vector, from Amazon Titan Multimodal Embeddings (the model that also embeds the query text at search time,
    through the collection's connector, so the two share one vector space). `bedrock_runtime` is a boto3 client for a Region
    that offers the model (us-east-1 or us-west-2)."""
    import base64

    body = {"inputImage": base64.b64encode(Path(path).read_bytes()).decode(),
            "embeddingConfig": {"outputEmbeddingLength": VECTOR_DIMENSIONS}}
    resp = bedrock_runtime.invoke_model(modelId=MULTIMODAL_MODEL, body=json.dumps(body),
                                        contentType="application/json", accept="application/json")
    vector = json.loads(resp["body"].read())["embedding"]
    if len(vector) != VECTOR_DIMENSIONS:
        raise ValueError(f"expected {VECTOR_DIMENSIONS} numbers, got {len(vector)}")
    return vector


def picture_vectors(bedrock_runtime, product_ids: list[str]) -> dict[str, list[float]]:
    """Vectors for the products in `product_ids` that have a picture (the others are left out)."""
    found = {}
    for product_id in product_ids:
        path = image_for(product_id)
        if path:
            found[product_id] = embed_picture(bedrock_runtime, path)
    return found

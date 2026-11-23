# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Catalog construction from the public Amazon Shopping Queries (ESCI) dataset.

Selection: US-locale products judged in the small-version test split, plus every piggy bank and coin
bank (the demo story needs them). Price and stock are SYNTHETIC: deterministic from the product ID and
labelled `price_synthetic: true`. Four story products are overridden by hand.
"""

from __future__ import annotations

import hashlib
import json
import math

# product_id: (price, in_stock, role in the story)
STORY: dict[str, tuple[float, bool, str]] = {
    "B07QRXG93G": (24.99, True, "hero: MASSJOY resin T-rex piggy bank, sold as a boy birthday present"),
    "B0778BMYZX": (27.99, False, "green variant for today: WAIT FLY green dinosaur resin bank, out of stock"),
    "B083R6K3G2": (18.96, True, "cheaper alternative: Linzy plush triceratops coin bank"),
    "B08DN9JR9D": (33.19, True, "over the $30 budget, inside $40: Noetoy musical dinosaur stealing coin bank"),
}

# One product that is NOT in the ESCI dataset, added so the $40 budget beat has a better, dearer answer. It has its own
# id prefix and `source`, and the app says it is added for the demo. Not in any evaluation judgment.
DEMO_PRODUCTS: list[dict] = [{
    "product_id": "DEMO-DINO-01",
    "title": "Roaring T-Rex Light-Up Dinosaur Piggy Bank with Sound, Large Electronic Coin Bank for Kids",
    "description": "A large electronic dinosaur piggy bank that lights up and roars when a coin drops in.",
    "bullet_points": "Large dinosaur money bank for kids who are saving up. Lights and roaring sound when a coin goes in. "
                     "Sturdy resin body, easy-open base. A birthday gift for boys and girls.",
    "brand": "Sample Demo", "color": "Green", "price": 38.99, "in_stock": True, "price_synthetic": True,
    "source": "sample-agent-ret-demo", "story_role": "inside the $40 budget: the dearer, better dinosaur bank added for the demo",
}]


def synth(product_id: str) -> tuple[float, bool]:
    """Deterministic synthetic (price, in_stock) for a product ID."""
    h = int(hashlib.sha256(product_id.encode()).hexdigest(), 16)
    price = round(5 + (h % 19500) / 100.0 * ((h >> 20) % 3 + 1) / 3, 2)
    return price, (h >> 40) % 10 != 0


def _s(value) -> str | None:
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)


def select_products(examples, products):
    """examples/products are the two ESCI dataframes. Returns the selected products dataframe."""
    test = examples[(examples.product_locale == "us") & (examples.small_version == 1) & (examples.split == "test")]
    products = products[products.product_locale == "us"]
    title = products.product_title.fillna("").str.lower()
    keep = products.product_id.isin(set(test.product_id)) | title.str.contains("piggy bank") | title.str.contains("coin bank")
    return products[keep]


def build_doc(row: dict) -> dict:
    price, in_stock = synth(row["product_id"])
    doc = {
        "product_id": row["product_id"], "title": _s(row["product_title"]), "description": _s(row["product_description"]),
        "bullet_points": _s(row["product_bullet_point"]) or "", "brand": _s(row["product_brand"]),
        "color": _s(row["product_color"]), "price": price, "in_stock": in_stock, "price_synthetic": True,
        "source": "esci-us",
    }
    if row["product_id"] in STORY:
        doc["price"], doc["in_stock"], doc["story_role"] = STORY[row["product_id"]]
    return doc


EMBED_LIMIT = 600  # characters of "title. bullet points" that are embedded


def embed_text(doc: dict) -> str:
    """The text the ingest pipeline turns into the product's vector: the title and the bullet points."""
    return f"{doc.get('title') or ''}. {doc.get('bullet_points') or ''}"[:EMBED_LIMIT]


def bulk_body(rows: list[dict], index: str, with_embed_text: bool = False,
              image_vectors: dict[str, list[float]] | None = None) -> str:
    """The _bulk request body. With `with_embed_text` each document carries the `embed_text` field the Serverless ingest
    pipeline embeds (a Serverless pipeline may hold only a text_embedding processor, so the field is built here).
    `image_vectors` maps a product id to its picture vector; those products get an `image_embedding` field."""
    lines = []
    for row in rows:
        doc = build_doc(row)
        if with_embed_text:
            doc["embed_text"] = embed_text(doc)
        if image_vectors and row["product_id"] in image_vectors:
            doc["image_embedding"] = image_vectors[row["product_id"]]
        lines.append(json.dumps({"index": {"_index": index, "_id": row["product_id"]}}))
        lines.append(json.dumps(doc))
    return "\n".join(lines) + "\n"

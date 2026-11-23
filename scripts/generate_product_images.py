#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate illustrative product pictures for the showcase subset of the catalog.

The ESCI dataset has no pictures, and the Amazon Berkeley Objects dataset (CC BY 4.0) matches only 560 of the 165,187 products
and none of the demo's. So pictures are generated from each product's title with Stable Image Core on Amazon Bedrock and
labelled illustrative in the app. The showcase subset: every piggy bank and coin bank, the story products and the demo-only
product. Output: assets/products/<product id>.jpg (256 px) and assets/products/manifest.json (id -> file name).

Resumable (skips ids already in the manifest). Usage:
  python scripts/generate_product_images.py [--limit N] [--workers 4] [--dry-run]
Needs AWS credentials with bedrock:InvokeModel in --region (default us-west-2; the model is not offered in every Region).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from retail_agent_demo.catalog import DEMO_PRODUCTS, STORY, select_products  # noqa: E402

OUT = ROOT / "assets" / "products"
MODEL = "stability.stable-image-core-v1:1"
SIZE = 256


THEMES = ("dino", "t-rex", "trex", "tyranno", "triceratops", "godzilla", "dragon", "monster")


def is_bank(title: str) -> bool:
    t = title.lower()
    return any(w in t for w in ("piggy", "coin bank", "money bank", "coin box", "money box", "saving bank", "savings bank"))


NEGATIVE = "text, watermark, people, hands, multiple objects"
THEMED_NEGATIVE = NEGATIVE + ", pig, pig nose, pig snout, pig ears, piglet, swine, boar, hippo, action figure, standing toy figure"


TITLE_HINTS = {"B07QRXG93G": " T-Rex"}  # the catalog's story calls the MASSJOY bank a T-rex; its title does not say so


def theme_of(title: str) -> str | None:
    """'trex', 'triceratops', 'monster' or 'dinosaur' for a dinosaur-themed bank, else None."""
    t = title.lower()
    if not is_bank(t) and "coin" not in t:
        return None
    for key, theme in (("triceratops", "triceratops"), ("t-rex", "trex"), ("trex", "trex"), ("tyranno", "trex"),
                       ("godzilla", "monster"), ("monster", "monster"), ("dino", "dinosaur")):
        if key in t:
            return theme
    return None


THEMED = {
    "trex": "a T-Rex dinosaur", "triceratops": "a plush triceratops dinosaur", "monster": "a cute monster dinosaur",
    "dinosaur": "a green dinosaur"}


# Each dinosaur bank looks different: its own creature, colours and pose (two near-identical prompts drew the same picture).
STYLE_HINTS = {
    "DEMO-DINO-01": "an orange and green T-Rex dinosaur with glowing LED lights along its back and its mouth open in a roar",
    "B07QRXG93G": "an olive-green T-Rex dinosaur in matte painted resin with a cream belly and a friendly closed-mouth smile",
    "B0778BMYZX": "a bright lime-green stegosaurus-style dinosaur with large pale plates along its back",
    "B074RJTGLR": "a dark charcoal-and-blue Godzilla-style monster dinosaur with orange spikes along its back",
    "B08DN9JR9D": "a cute purple cartoon monster dinosaur with big round eyes and one hand reaching out to grab a coin",
}


def seed_for(product_id: str) -> int:
    """A fixed random seed per product, so two products with similar prompts still get different pictures."""
    return int(hashlib.sha256(product_id.encode()).hexdigest()[:8], 16) % 4294967295


def themed_prompt(title: str, theme: str, hint: str = "") -> str:
    """A bank SHAPED like the creature, described without the title's word 'piggy' (it makes the model draw a pig): the
    creature is plainly a dinosaur (head with a snout and teeth, spines along the back, a thick tail), the body is rounded,
    and a coin slot and a few coins say it is a money bank."""
    material = "soft plush toy" if theme == "triceratops" else "glossy ceramic"
    features = "a triceratops head with three horns and a bony frill" if theme == "triceratops" else \
        "a dinosaur head with a long snout and teeth, small arms, spines along the back and a thick tail"
    creature = hint or THEMED[theme]
    return (f"Product photo of a {material} coin bank shaped like {creature}: {features}, and a chubby rounded body. "
            "A narrow coin slot is cut along its back, and a few gold coins rest beside it. "
            "A dinosaur coin bank, clearly a dinosaur. Single object, centred, studio lighting, plain white background, no text, no people.")


def prompt_for(title: str, hint: str = "") -> str:
    """A catalog-photo prompt from the product title (long titles are cut: the model reads a short prompt). A dinosaur-themed
    bank gets a prompt that describes the creature as a coin bank (see themed_prompt); any other bank is asked to read as a
    bank, with a visible coin slot and coins beside it."""
    title = " ".join(str(title).split())[:140]
    theme = theme_of(title)
    if theme:
        return themed_prompt(title, theme, hint)
    extra = " It is a money bank with a clearly visible coin slot on its back and a few gold coins resting beside it." if is_bank(title) else ""
    return f"Product photo of {title}.{extra} Single object, centred, studio lighting, plain white background, no text, no people."


def fallback_prompt(title: str) -> str:
    """A neutral prompt by product kind, for titles the model's prompt filter refuses (it reads some titles as unsafe)."""
    t = title.lower()
    if "piggy" in t or "pig " in t or "bank" in t:
        kind = "a plain ceramic piggy bank"
    elif "coin" in t:
        kind = "a small pile of old silver coins"
    elif any(w in t for w in ("book", "guide", "notebook", "diary", "recipes", "journal", "workbook")):
        kind = "a closed hardcover book with a plain abstract cover"
    else:
        kind = "a simple gift item"
    return f"Product photo of {kind}. Single object, centred, studio lighting, plain white background, no text, no people."


def showcase(examples, products) -> list[tuple[str, str]]:
    """[(product id, title)] for the showcase: every piggy and coin bank, the story and demo products, and everything the demo's
    queries can put on screen (assets/showcase-ids.json, from scripts/showcase_products.py). Story products first."""
    catalog = select_products(examples, products)
    title = catalog.product_title.fillna("").str.lower()
    banks = catalog[title.str.contains("piggy bank") | title.str.contains("coin bank")]
    rows = {r.product_id: r.product_title for r in catalog[catalog.product_id.isin(STORY)].itertuples()}
    rows.update({r.product_id: r.product_title for r in banks.itertuples()})
    rows.update({d["product_id"]: d["title"] for d in DEMO_PRODUCTS})
    listed = ROOT / "assets" / "showcase-ids.json"
    if listed.exists():
        rows.update(json.loads(listed.read_text()))
    return sorted(rows.items(), key=lambda kv: (kv[0] not in STORY and not kv[0].startswith("DEMO"), kv[0]))


def to_jpeg(png_or_jpeg: bytes, size: int = SIZE) -> bytes:
    from PIL import Image
    img = Image.open(io.BytesIO(png_or_jpeg)).convert("RGB")
    img.thumbnail((size, size))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82, optimize=True)
    return out.getvalue()


def generate(client, title: str, retries: int = 4, seed: int | None = None, hint: str = "") -> bytes:
    """One picture. If the filter refuses the title's prompt, try a neutral prompt for the product kind."""
    try:
        return _generate(client, prompt_for(title, hint), retries, seed)
    except RuntimeError as e:
        if "filtered" not in str(e):
            raise
        return _generate(client, fallback_prompt(title), retries, seed)


def _generate(client, prompt: str, retries: int, seed: int | None = None) -> bytes:
    request = {"prompt": prompt, "aspect_ratio": "1:1", "output_format": "jpeg",
               "negative_prompt": THEMED_NEGATIVE if "coin bank shaped like" in prompt else NEGATIVE}
    if seed is not None:
        request["seed"] = seed
    body = json.dumps(request)
    for attempt in range(retries):
        try:
            resp = json.loads(client.invoke_model(modelId=MODEL, body=body)["body"].read())
            if resp.get("finish_reasons") and resp["finish_reasons"][0]:
                raise RuntimeError(f"filtered: {resp['finish_reasons'][0]}")
            return to_jpeg(base64.b64decode(resp["images"][0]))
        except Exception as e:  # noqa: BLE001 - throttling and transient errors: back off and retry
            if attempt == retries - 1 or "filtered" in str(e):
                raise RuntimeError(str(e)) from e
            time.sleep(2 ** attempt * 2)
    raise RuntimeError("unreachable")


def main() -> None:
    import boto3
    import pandas as pd
    from botocore.config import Config

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="stop after this many new pictures (0 = all)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--region", default="us-west-2")
    ap.add_argument("--dry-run", action="store_true", help="list what would be generated")
    ap.add_argument("--redo", nargs="*", help="regenerate these product ids even if they have a picture (after a prompt change)")
    args = ap.parse_args()

    rows = showcase(pd.read_parquet(ROOT / "data/esci_examples.parquet"), pd.read_parquet(ROOT / "data/esci_products.parquet"))
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    todo = [(pid, t) for pid, t in rows if pid not in manifest or not (OUT / manifest[pid]).exists()]
    if args.redo:
        titles = dict(rows)
        todo = [(pid, titles.get(pid, "")) for pid in args.redo]
    already = len(rows) - len(todo)
    if args.limit:
        todo = todo[: args.limit]
    print(f"showcase: {len(rows)} products, {already} done, {len(todo)} to generate now", flush=True)
    if args.dry_run:
        for pid, t in todo[:10]:
            print(" ", pid, prompt_for(t)[:110])
        return
    client = boto3.client("bedrock-runtime", region_name=args.region, config=Config(read_timeout=120, retries={"max_attempts": 2}))
    lock, failed, done = threading.Lock(), [], 0

    def work(item: tuple[str, str]) -> None:
        nonlocal done
        pid, title = item
        try:
            data = generate(client, title + TITLE_HINTS.get(pid, ""), seed=seed_for(pid), hint=STYLE_HINTS.get(pid, ""))
        except Exception as e:  # noqa: BLE001
            with lock:
                failed.append((pid, str(e)[:100]))
            return
        (OUT / f"{pid}.jpg").write_bytes(data)
        with lock:
            manifest[pid] = f"{pid}.jpg"
            done += 1
            if done % 10 == 0:
                manifest_path.write_text(json.dumps(manifest, indent=0, sort_keys=True))
                print(f"  {done}/{len(todo)}", flush=True)

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(work, todo))
    manifest_path.write_text(json.dumps(manifest, indent=0, sort_keys=True))
    print(f"generated {done}, failed {len(failed)}: {failed[:5]}")


if __name__ == "__main__":
    main()

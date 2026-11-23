# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Synthetic warehouse data for the Sample inventory demo (shared by the inventory tools).

Deterministic from a hash of the product id. No `from __future__` import on purpose: infra/cloudformation/
inventory-gateway.yaml inlines this file and gateway_handler.py into one Lambda module. Standard library only.
"""

import hashlib
import re

WAREHOUSES = ("Columbus, OH", "Reno, NV", "Allentown, PA", "Dallas, TX", "Atlanta, GA")
PRODUCT_ID = re.compile(r"^[A-Za-z0-9]{1,20}$")

# Hand-set for the demo story; everything else comes from the hash.
FIXED = {
    "B07QRXG93G": {"in_stock": True, "quantity": 42, "warehouse": "Columbus, OH", "days": 2},   # MASSJOY T-rex bank
    "B0778BMYZX": {"in_stock": False, "quantity": 0, "warehouse": "Reno, NV", "days": 21},      # WAIT FLY green dinosaur bank
}


def stock_record(product_id: str) -> dict:
    """Deterministic synthetic stock for a product id (same id, same answer, every time)."""
    pid = product_id.upper()
    if pid in FIXED:
        return dict(FIXED[pid])
    d = hashlib.sha256(pid.encode()).digest()
    in_stock = d[0] % 5 != 0  # about 80 percent in stock
    quantity = 1 + d[1] % 120 if in_stock else 0
    days = (1 + d[2] % 5) if in_stock else (14 + d[2] % 15)
    return {"in_stock": in_stock, "quantity": quantity, "warehouse": WAREHOUSES[d[3] % len(WAREHOUSES)], "days": days}


def check_stock(product_id: str) -> dict:
    r = stock_record(product_id)
    return {"product_id": product_id, "in_stock": r["in_stock"], "quantity": r["quantity"], "warehouse": r["warehouse"]}


def delivery_estimate(product_id: str) -> dict:
    return {"product_id": product_id, "days": stock_record(product_id)["days"]}

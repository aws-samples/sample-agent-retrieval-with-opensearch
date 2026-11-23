# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pull a price ceiling out of a shopper's sentence for the direct-search versions (v0, v1).

The agent versions plan their own price filter; this is only for the paths that search directly.
"""

from __future__ import annotations

import re

NO_LIMIT = 100000.0
_AMOUNT = r"\$?\s*(\d+(?:\.\d+)?)"
# A phrase that states the ceiling wins over any other dollar amount in the sentence.
_PHRASED = re.compile(rf"(?:under|below|less than|up to|budget(?: is| of)?|max(?:imum)?(?: of)?)\s*{_AMOUNT}", re.I)
_DOLLAR = re.compile(r"\$\s*(\d+(?:\.\d+)?)")


def parse_budget(text: str) -> tuple[float, float]:
    """Return (min_price, max_price). Decimals are kept; with no stated ceiling the range is open."""
    m = _PHRASED.search(text) or _DOLLAR.search(text)
    return (0.0, float(m.group(1))) if m else (0.0, NO_LIMIT)

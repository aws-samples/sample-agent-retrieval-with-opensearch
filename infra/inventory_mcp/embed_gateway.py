#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Inline inventory_data.py + gateway_handler.py into the `ZipFile` block of infra/cloudformation/inventory-gateway.yaml.

A Lambda `ZipFile` is a single module, so the shared data module and the handler are concatenated (the handler's
`shared-import` line is dropped). A unit test fails if the template drifts from the sources.
Usage: python infra/inventory_mcp/embed_gateway.py
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "cloudformation" / "inventory-gateway.yaml"
INDENT = " " * 10
BLOCK = re.compile(r"(?P<head>^ {8}ZipFile: \|\n)(?P<body>(?:(?: {10}.*)?\n)+)", re.M)


def embed(template: str, source: str) -> str:
    body = "".join((INDENT + line if line.strip() else "") + "\n" for line in source.rstrip("\n").split("\n")) + "\n"
    new, n = BLOCK.subn(lambda m: m.group("head") + body, template, count=1)
    if n != 1:
        raise SystemExit("ZipFile block not found in the template")
    return new


def embedded(template: str) -> str:
    """The source currently inlined in the template, de-indented."""
    m = BLOCK.search(template)
    if not m:
        raise SystemExit("ZipFile block not found in the template")
    lines = m.group("body").rstrip("\n").split("\n")
    return "\n".join(line[len(INDENT):] if line.strip() else "" for line in lines) + "\n"


def combined_source() -> str:
    data = (HERE / "inventory_data.py").read_text().rstrip("\n")
    handler = "\n".join(line for line in (HERE / "gateway_handler.py").read_text().split("\n")
                        if "shared-import" not in line).strip("\n")
    return data + "\n\n\n" + handler + "\n"


if __name__ == "__main__":
    TEMPLATE.write_text(embed(TEMPLATE.read_text(), combined_source()))
    print(f"embedded inventory_data.py + gateway_handler.py into {TEMPLATE.name}")

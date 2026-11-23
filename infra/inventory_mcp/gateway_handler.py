# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Inventory tools as an Amazon Bedrock AgentCore Gateway Lambda target .

The Gateway speaks MCP to the caller and invokes this function once per tool call, using its own service role (so
the function has no public resource policy). Contract: the event is the tool's arguments (a JSON object); the tool
name is in context.client_context.custom["bedrockAgentCoreToolName"], possibly prefixed "<target>___<tool>".
The return value becomes the tool result. Synthetic data only; see inventory_data.py. Standard library only.
"""

import json

from inventory_data import PRODUCT_ID, check_stock, delivery_estimate  # shared-import (stripped when inlined)

IMPLEMENTATIONS = {"check_stock": check_stock, "delivery_estimate": delivery_estimate}
SEPARATOR = "___"


def tool_name(context) -> str:
    """The tool the Gateway is calling, without the target-name prefix. Empty string if it cannot be found."""
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    name = custom.get("bedrockAgentCoreToolName") or ""
    return name.split(SEPARATOR, 1)[1] if SEPARATOR in name else name


def lambda_handler(event, context=None):
    name = tool_name(context)
    fn = IMPLEMENTATIONS.get(name)
    # One line per call: the tool and its outcome. No arguments beyond the (synthetic) product id, no credentials.
    pid = event.get("product_id") if isinstance(event, dict) else None
    print(json.dumps({"tool": name[:40], "known": fn is not None, "valid_id": isinstance(pid, str) and bool(PRODUCT_ID.match(pid))}))
    if fn is None:
        return {"error": f"unknown tool: {name[:40]}"}
    if not isinstance(pid, str) or not PRODUCT_ID.match(pid):
        return {"error": "product_id must be 1-20 letters or digits, for example B07QRXG93G"}
    return fn(pid)

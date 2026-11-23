# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The AgentCore Gateway variant of the inventory tools. No network."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
MCP_DIR = ROOT / "infra" / "inventory_mcp"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(MCP_DIR))  # gateway_handler imports inventory_data, as the inlined Lambda module has it in scope
data = load("inventory_data", MCP_DIR / "inventory_data.py")
gw = load("gateway_handler", MCP_DIR / "gateway_handler.py")
embed = load("embed_gateway", MCP_DIR / "embed_gateway.py")
TEMPLATE = (ROOT / "infra" / "cloudformation" / "inventory-gateway.yaml").read_text()


def ctx(tool):
    return SimpleNamespace(client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": tool}))


@pytest.mark.parametrize("name", ["check_stock", "inventory___check_stock", "any-target___check_stock"])
def test_the_story_products_with_or_without_the_target_prefix(name):
    assert gw.lambda_handler({"product_id": "B07QRXG93G"}, ctx(name)) == {
        "product_id": "B07QRXG93G", "in_stock": True, "quantity": 42, "warehouse": "Columbus, OH"}
    out = gw.lambda_handler({"product_id": "B0778BMYZX"}, ctx(name))
    assert out["in_stock"] is False and out["warehouse"] == "Reno, NV"


def test_delivery_estimate():
    assert gw.lambda_handler({"product_id": "B07QRXG93G"}, ctx("inventory___delivery_estimate")) == {
        "product_id": "B07QRXG93G", "days": 2}


@pytest.mark.parametrize("bad", [None, 5, "", "has space", "x" * 21, "../etc"])
def test_bad_ids_and_unknown_tools_are_errors_not_crashes(bad):
    assert "error" in gw.lambda_handler({"product_id": bad}, ctx("inventory___check_stock"))
    assert "error" in gw.lambda_handler({"product_id": "B07QRXG93G"}, ctx("inventory___nope"))
    assert "error" in gw.lambda_handler({"product_id": "B07QRXG93G"}, None)
    assert "error" in gw.lambda_handler("not a dict", ctx("check_stock"))


def test_the_story_products_are_fixed():
    assert data.check_stock("B07QRXG93G") == {
        "product_id": "B07QRXG93G", "in_stock": True, "quantity": 42, "warehouse": "Columbus, OH"}
    out = data.check_stock("B0778BMYZX")  # WAIT FLY green dinosaur bank
    assert out["in_stock"] is False and out["quantity"] == 0 and out["warehouse"] == "Reno, NV"
    assert data.delivery_estimate("B07QRXG93G")["days"] == 2


def test_other_products_are_deterministic_and_consistent_between_the_tools():
    for pid in ("B000000001", "B0ABCDEF12", "b0abcdef12", "X"):  # pragma: allowlist secret
        a, b = data.check_stock(pid), data.check_stock(pid)
        assert a == b and a["warehouse"] in data.WAREHOUSES
        assert a["in_stock"] == (a["quantity"] > 0)
        days = data.delivery_estimate(pid)["days"]
        assert (1 <= days <= 5) if a["in_stock"] else (14 <= days <= 28)


def test_about_four_fifths_of_synthetic_products_are_in_stock():
    share = sum(data.check_stock(f"B{i:09d}")["in_stock"] for i in range(500)) / 500
    assert 0.7 < share < 0.9


@pytest.mark.parametrize("bad", [None, 5, "", "has space", "x" * 21, "../etc", "B07;drop"])
def test_bad_product_ids_are_rejected(bad):
    assert not (isinstance(bad, str) and data.PRODUCT_ID.match(bad))
    assert "error" in gw.lambda_handler({"product_id": bad}, ctx("check_stock"))


def test_the_template_inlines_the_current_sources(tmp_path):
    assert embed.embedded(TEMPLATE) == embed.combined_source(), "run: python infra/inventory_mcp/embed_gateway.py"
    # the single module must stand alone: write it out and import it as a module of its own
    module_file = tmp_path / "inlined_gateway.py"
    module_file.write_text(embed.combined_source())
    spec = importlib.util.spec_from_file_location("inlined_gateway", module_file)
    inlined = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inlined)
    assert inlined.lambda_handler({"product_id": "B07QRXG93G"}, ctx("inventory___check_stock"))["quantity"] == 42


def test_the_lambda_has_no_resource_policy_and_the_gateway_role_is_scoped():
    assert "AWS::Lambda::Permission" not in TEMPLATE and "AWS::Lambda::Url" not in TEMPLATE
    assert 'Principal: "*"' not in TEMPLATE and "Principal: '*'" not in TEMPLATE
    assert "Action: lambda:InvokeFunction\n            Resource: !GetAtt Function.Arn" in TEMPLATE
    assert "AuthorizerType: CUSTOM_JWT" in TEMPLATE and "AuthorizerType: NONE" not in TEMPLATE
    assert "ReservedConcurrentExecutions: 5" in TEMPLATE and "project" in TEMPLATE

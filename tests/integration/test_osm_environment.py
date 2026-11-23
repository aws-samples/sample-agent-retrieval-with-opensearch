# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Is the environment for the Serverless + OpenSearch Memory app ready? Checks the pieces the app needs, one by one, with a message that says
which piece is missing.

Run: RUN_INTEGRATION=1 pytest tests/integration/test_osm_environment.py
Set APP_URL (for example http://127.0.0.1:8505) to check that the app endpoint itself is up.
Needs AWS credentials for your deployed stack and the SSM port forward to the collection (docs/setup.md).
"""

import importlib.util
import os
import time
from pathlib import Path

import boto3
import pytest
import requests

from retail_agent_demo import agentcore as ac
from retail_agent_demo import osmemory as osm
from retail_agent_demo.config import load_settings
from retail_agent_demo.os_client import OSClient

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="set RUN_INTEGRATION=1 to run against your deployed stack")

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("deploy_agentcore", ROOT / "infra/serverless/deploy_agentcore.py")
dep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dep)


@pytest.fixture(scope="module")
def s():
    return load_settings()


@pytest.fixture(scope="module")
def memory(s):
    return osm.new_client(s.ids.get("memory_region", "us-east-1"))


def test_the_ids_file_names_the_memory_space_and_its_region(s):
    assert s.ids.get("space_id", "").startswith("sp-"), "config/ids.serverless.json has no space_id: run infra/serverless/deploy_agentcore.py"
    assert s.ids.get("memory_region") in ("us-east-1", "us-west-2", "ap-northeast-1", "eu-central-1")


def test_the_space_is_active_and_matches_what_the_deploy_script_defines(memory, s):
    sp = memory.get_space(spaceId=s.id("space_id"))
    assert sp["status"] == "ACTIVE", sp.get("statusReason")
    assert sp["name"] == dep.SPACE_NAME
    assert sp["memoryStrategies"] == dep.SPACE_STRATEGIES, "the live strategy differs from SPACE_STRATEGIES: update-space or fix the constant"
    assert sp.get("extractionModelId") == dep.SPACE_EXTRACTION_MODEL


def test_a_conversation_becomes_a_searchable_memory_within_a_minute_and_forgetting_leaves_nothing(memory, s):
    space, actor = s.id("space_id"), "env-check"
    try:
        t0 = time.time()
        osm.store_turn(memory, space, actor, ac.new_session_id(), [
            {"role": "user", "text": "My daughter Mia is nine and loves astronomy. My budget is 45 dollars."},
            {"role": "assistant", "text": "Noted: a gift for Mia, nine, who loves astronomy, up to 45 dollars."}])
        assert osm.wait_for_records(memory, space, actor, minimum=1, seconds=90) >= 1, "no memory after 90 s: is the extraction model reachable?"
        seconds = time.time() - t0
        found = []
        for _ in range(12):
            found = osm.recall(memory, space, actor, "who is the gift for?", 5)
            if any("Mia" in f for f in found):
                break
            time.sleep(5)
        assert any("Mia" in f for f in found), found
        assert seconds < 90
    finally:
        out = osm.forget_shopper_verified(memory, space, actor)
    assert out["left over"] == 0


def test_the_collection_is_reachable_through_the_tunnel(s):
    count = OSClient(s, timeout=60).post(f"/{s.ids['products_index']}/_count", {})["count"]
    assert count > 100_000, "the catalog is missing or not fully copied"


def test_the_hosted_agent_runtime_is_ready(s):
    rt = boto3.client("bedrock-agentcore-control", region_name=s.region).get_agent_runtime(agentRuntimeId=s.id("agent_runtime_id"))
    assert rt["status"] == "READY", rt.get("failureReason")


def test_the_catalog_gateway_answers_with_a_token(s):
    cognito = boto3.client("cognito-idp", region_name=s.region)
    secret = cognito.describe_user_pool_client(UserPoolId=s.id("catalog_user_pool_id"), ClientId=s.id("catalog_client_id"))["UserPoolClient"]["ClientSecret"]
    token = ac.cognito_token(s.id("catalog_token_endpoint"), s.id("catalog_client_id"), secret, "catalog/search")
    products, trace = ac.search_products_via_gateway(ac.GatewayHttp(s.id("catalog_gateway_url"), token), "dinosaur piggy bank", 0, 30)
    assert products and [x["method"] for x in trace.steps] == ["initialize", "tools/list", "tools/call"]


@pytest.mark.skipif(not os.environ.get("APP_URL"), reason="set APP_URL to check the app endpoint")
def test_the_app_endpoint_is_healthy():
    url = os.environ["APP_URL"].rstrip("/")
    assert requests.get(f"{url}/_stcore/health", timeout=10).text.strip() == "ok", f"{url} is not serving the app"

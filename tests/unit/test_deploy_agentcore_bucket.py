# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The AgentCore code bucket: owned by this account, locked down on every run, never written to if another account owns it."""

import importlib.util
import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("deploy_agentcore", ROOT / "infra/serverless/deploy_agentcore.py")
dep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dep)

ACCOUNT = "test-account"  # a label: the fakes only compare it, and a number shaped like an account id is flagged by the secret scanners


class FakeS3:
    def __init__(self, head_error=None):
        self.head_error, self.calls = head_error, []

    def head_bucket(self, **kw):
        self.calls.append(("head_bucket", kw))
        if self.head_error:
            raise ClientError({"Error": {"Code": self.head_error}}, "HeadBucket")

    def __getattr__(self, name):
        def call(**kw):
            self.calls.append((name, kw))
        return call


def names(s3):
    return [n for n, _ in s3.calls]


def test_an_owned_bucket_is_locked_down_again_on_every_run():
    s3 = FakeS3()
    dep.ensure_bucket(s3, "b", ACCOUNT)
    assert names(s3) == ["head_bucket", "put_public_access_block", "put_bucket_encryption", "put_bucket_policy"]


def test_a_missing_bucket_is_created_then_locked_down():
    s3 = FakeS3("404")
    dep.ensure_bucket(s3, "b", ACCOUNT)
    assert names(s3)[:2] == ["head_bucket", "create_bucket"] and "put_bucket_policy" in names(s3)


@pytest.mark.parametrize("code", ["403", "AccessDenied"])
def test_a_bucket_owned_by_someone_else_is_refused(code):
    s3 = FakeS3(code)
    with pytest.raises(SystemExit):
        dep.ensure_bucket(s3, "b", ACCOUNT)
    assert names(s3) == ["head_bucket"]  # nothing was written


def test_every_call_names_the_expected_owner_and_the_policy_denies_plain_http():
    s3 = FakeS3()
    dep.ensure_bucket(s3, "b", ACCOUNT)
    assert all(kw.get("ExpectedBucketOwner") == ACCOUNT for _, kw in s3.calls)
    policy = json.loads(next(kw for n, kw in s3.calls if n == "put_bucket_policy")["Policy"])
    stmt = policy["Statement"][0]
    assert stmt["Effect"] == "Deny" and stmt["Condition"] == {"Bool": {"aws:SecureTransport": "false"}}
    assert "arn:aws:s3:::b/*" in stmt["Resource"]


def test_upload_names_the_expected_owner():
    s3 = FakeS3()
    key = dep.upload(s3, "b", "agent-runtime", b"zip", ACCOUNT)
    assert key.startswith("agent-runtime-") and key.endswith(".zip")
    assert s3.calls[0][1]["ExpectedBucketOwner"] == ACCOUNT


def test_runtime_dependencies_are_all_pinned():
    lines = [x.strip() for x in dep.AGENT_REQUIREMENTS.read_text().splitlines() if x.strip() and not x.startswith("#")]
    assert len(lines) > 20 and all("==" in x for x in lines)
    assert any(x.startswith("mcp==1.") for x in lines)


class FakeSpaces:
    """OpenSearch Memory control plane: list_spaces pages, create_space, and a waiter."""

    def __init__(self, spaces=()):
        self.spaces, self.created, self.waited = list(spaces), [], []

    def list_spaces(self, **kw):
        if kw.get("nextToken"):
            return {"spaces": self.spaces[1:]}
        return {"spaces": self.spaces[:1], **({"nextToken": "t"} if len(self.spaces) > 1 else {})}

    def create_space(self, **kw):
        self.created.append(kw)
        return {"spaceId": "sp-new", "status": "CREATING"}

    def get_waiter(self, name):
        owner = self
        return type("W", (), {"wait": lambda self, **kw: owner.waited.append((name, kw["spaceId"]))})()


def test_the_memory_space_is_created_once_with_the_demo_strategy_and_a_pinned_model():
    osm = FakeSpaces()
    assert dep.ensure_memory_space(osm) == "sp-new"
    made = osm.created[0]
    assert made["name"] == "sample-agent-ret-shopper-memory" and made["extractionModelId"] == "anthropic.claude-haiku-5-5"
    strategy = made["memoryStrategies"][0]
    assert strategy["type"] == "CUSTOM" and strategy["name"] == "shopper-facts" and strategy["configuration"]["consolidationEnabled"] is True
    prompt = strategy["configuration"]["extractionPrompt"]
    assert "Do not write a note about the budget" in prompt and "application records it" in prompt  # the app writes the budget (osmemory.set_budget)
    assert len(prompt) < 30720  # the service's limit
    assert osm.waited == [("space_active", "sp-new")]


def test_an_existing_space_is_reused_even_on_a_later_page_and_a_deleting_one_is_not():
    other = {"spaceId": "sp-other", "name": "something-else", "status": "ACTIVE"}
    ours = {"spaceId": "sp-ours", "name": "sample-agent-ret-shopper-memory", "status": "ACTIVE"}
    osm = FakeSpaces([other, ours])
    assert dep.ensure_memory_space(osm) == "sp-ours" and osm.created == []
    dying = FakeSpaces([{**ours, "status": "DELETING"}])
    assert dep.ensure_memory_space(dying) == "sp-new" and len(dying.created) == 1


@pytest.mark.parametrize("region,expected", [("us-east-1", {}), ("us-east-2", {"CreateBucketConfiguration": {"LocationConstraint": "us-east-2"}})])
def test_only_regions_other_than_us_east_1_send_a_location_constraint(monkeypatch, region, expected):
    monkeypatch.setattr(dep, "REGION", region)
    s3 = FakeS3("404")
    dep.ensure_bucket(s3, "b", ACCOUNT)
    create = next(kw for n, kw in s3.calls if n == "create_bucket")
    assert {k: v for k, v in create.items() if k != "Bucket"} == expected

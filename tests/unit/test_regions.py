# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The deploy scripts and templates follow the Region you choose instead of assuming one (no network)."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("deploy_agentcore", ROOT / "infra/serverless/deploy_agentcore.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


@pytest.mark.parametrize("region,prefix", [("us-east-1", "us"), ("us-west-2", "us"), ("eu-central-1", "eu"),
                                           ("eu-west-1", "eu"), ("ap-northeast-1", "apac"), ("ap-southeast-2", "apac")])
def test_the_model_profile_prefix_follows_the_geography(region, prefix):
    assert deploy.inference_profile_prefix(region) == prefix


def test_the_memory_service_regions_are_the_four_that_offer_it():
    assert set(deploy.MEMORY_REGIONS) == {"us-east-1", "us-west-2", "ap-northeast-1", "eu-central-1"}


def test_the_templates_do_not_pin_a_region_or_a_geography_prefix():
    template = (ROOT / "infra/serverless/agentcore.yaml").read_text()
    assert "inference-profile/us.anthropic" not in template  # the profile prefix differs by geography
    collection = (ROOT / "infra/serverless/collection.yaml").read_text()
    assert "us-east-2" not in collection and "us-east-2" not in template  # the Region is the stack's own

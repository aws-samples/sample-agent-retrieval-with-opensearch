# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json

import pytest

from retail_agent_demo import config
from retail_agent_demo.config import Settings


def make_settings(ids):
    return Settings(host="h", local_url="https://localhost:9200", region="us-east-1",  # nosec B106 - test double, not a real secret
                    auth="basic", user="u", password="p", verify_tls=False,
                    rerank_region="us-west-2", rerank_model_arn="arn", connector_role_arn=None, ids=ids)


def test_id_lookup():
    assert make_settings({"agent_id": "A1"}).id("agent_id") == "A1"


def test_missing_id_raises_with_guidance():
    with pytest.raises(KeyError, match="infra/"):
        make_settings({}).id("rerank_model_id")


def test_placeholder_id_rejected():
    with pytest.raises(KeyError):
        make_settings({"agent_id": "<conversational agent id>"}).id("agent_id")


def test_ids_example_is_valid_json_with_expected_keys():
    from retail_agent_demo.config import REPO_ROOT

    data = json.loads((REPO_ROOT / "config" / "ids.example.json").read_text())
    for key in ("products_index", "hybrid_pipeline", "embedding_model_id", "rerank_model_id", "space_id", "agent_runtime_arn"):
        assert key in data


def test_the_settings_default_to_a_sigv4_signed_serverless_collection(monkeypatch):
    for name in ("OS_AUTH", "OS_SERVICE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OS_HOST", "x.us-east-1.aoss.amazonaws.com")
    s = config.load_settings(env_file="/nonexistent")
    assert (s.auth, s.service) == ("sigv4", "aoss")
    assert config.ids_path().name == "ids.json" and config.env_path().name == ".env"


def test_the_rerank_model_arn_follows_the_rerank_region(monkeypatch):
    monkeypatch.setenv("OS_HOST", "x.eu-central-1.aoss.amazonaws.com")
    monkeypatch.setenv("RERANK_REGION", "eu-central-1")
    monkeypatch.delenv("RERANK_MODEL_ARN", raising=False)
    s = config.load_settings(env_file="/nonexistent")
    assert s.rerank_region == "eu-central-1" and "bedrock:eu-central-1::foundation-model/cohere.rerank" in s.rerank_model_arn

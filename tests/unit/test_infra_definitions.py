# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEFS = ROOT / "infra" / "serverless" / "opensearch"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(ROOT / "src"))
deploy = load_module("sls_deploy_objects", ROOT / "infra" / "serverless" / "deploy_objects.py")
deploy.render = deploy.render_local

# Every value deploy_objects.py can supply when it renders a definition.
PROVIDED = {"ROLE_ARN", "REGION", "PREFIX", "RERANK_REGION", "RERANK_MODEL_ARN", "EMBEDDING_MODEL_ID",
            "RERANK_MODEL_ID", "MULTIMODAL_MODEL_ID", "MULTIMODAL_REGION", "MULTIMODAL_ROLE_ARN"}
ALL_VALUES = {k: f"<{k}>" for k in PROVIDED} | {"PREFIX": "demo"}  # a prefix lands inside names, not <>-quoted
DEFINITIONS = sorted(p.relative_to(DEFS).as_posix() for p in DEFS.rglob("*.json"))


@pytest.mark.parametrize("path", DEFINITIONS)
def test_definition_is_valid_json_and_every_placeholder_is_supplied(path):
    text = (DEFS / path).read_text()
    assert set(re.findall(r"@@([A-Z_]+)@@", text)) <= PROVIDED
    assert deploy.render(path, ALL_VALUES)  # parses after substitution


def test_render_refuses_a_missing_value():
    with pytest.raises(SystemExit, match="ROLE_ARN"):
        deploy.render("connectors/titan-embedding.json", {})


def test_every_connector_uses_the_role_placeholder_not_a_literal_arn():
    for path in DEFS.glob("connectors/*.json"):
        credential = json.loads(path.read_text())["credential"]
        if path.name == "titan-multimodal-embedding.json":  # its own role: the main one is scoped to named models
            assert credential == {"roleArn": "@@MULTIMODAL_ROLE_ARN@@"}
        else:
            assert credential == {"roleArn": "@@ROLE_ARN@@"}


def test_no_account_ids_or_object_ids_are_committed_in_definitions():
    for path in DEFS.rglob("*.json"):
        text = path.read_text()
        assert not re.search(r"\b\d{12}\b", text), f"account id in {path.name}"
        assert not re.search(r"\bvpc-[0-9a-f]{8,}|\bsubnet-[0-9a-f]{8,}", text), f"vpc/subnet id in {path.name}"


class TestFindId:
    def client_returning(self, hits=None, error=None):
        client = MagicMock()
        if error:
            client.post.side_effect = error
        else:
            client.post.return_value = {"hits": {"hits": hits or []}}
        return client

    def test_returns_the_id_when_found(self):
        assert deploy.find_id(self.client_returning([{"_id": "abc"}]), "/x/_search", "n") == "abc"

    def test_returns_none_when_there_is_no_match(self):
        assert deploy.find_id(self.client_returning([]), "/x/_search", "n") is None

    def test_returns_none_only_when_the_backing_index_is_missing(self):
        err = OSError("POST /x/_search -> 404: no such index")
        assert deploy.find_id(self.client_returning(error=err), "/x/_search", "n") is None

    @pytest.mark.parametrize("error", [OSError("POST /x -> 503: unavailable"), OSError("Read timed out"),
                                       OSError("POST /x -> 403: forbidden")])
    def test_other_failures_are_raised_so_no_duplicate_is_created(self, error):
        with pytest.raises(OSError):
            deploy.find_id(self.client_returning(error=error), "/x/_search", "n")

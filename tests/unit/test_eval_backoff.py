# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import statistics
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evalr = load_module("eval_retrieval_backoff", ROOT / "scripts" / "eval_retrieval.py")

HITS = {"hits": {"hits": [{"_id": "a"}, {"_id": "b"}]}}


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(evalr.time, "sleep", lambda s: calls.append(s))
    return calls


class TestSearchIdsBackoff:
    @pytest.mark.parametrize("marker", ["POST /i/_search -> 429: slow down",
                                        "POST /i/_search -> 503: unavailable",
                                        "POST /i/_search -> 502: bad gateway",
                                        "read timed out",
                                        "POST /i/_search -> 500: ThrottlingException from Bedrock"])
    def test_each_transient_failure_is_retried(self, sleeps, marker):
        client = MagicMock()
        client.post.side_effect = [OSError(marker), HITS]
        assert evalr.search_ids(client, "idx", {"size": 1}, None) == ["a", "b"]
        assert client.post.call_count == 2
        assert sleeps == [1]

    def test_the_backoff_doubles_and_is_capped_at_twenty_seconds_until_the_eighth_failure(self, sleeps):
        client = MagicMock()
        client.post.side_effect = OSError("POST /i/_search -> 429: still throttled")
        with pytest.raises(OSError, match="429"):
            evalr.search_ids(client, "idx", {"size": 1}, None)
        assert client.post.call_count == 8
        assert sleeps == [1, 2, 4, 8, 16, 20, 20]

    @pytest.mark.parametrize("marker", ["POST /i/_search -> 400: bad query",
                                        "POST /i/_search -> 403: forbidden"])
    def test_a_permanent_error_raises_immediately_without_a_retry(self, sleeps, marker):
        client = MagicMock()
        client.post.side_effect = OSError(marker)
        with pytest.raises(OSError):
            evalr.search_ids(client, "idx", {"size": 1}, None)
        assert client.post.call_count == 1
        assert sleeps == []

    def test_the_search_pipeline_is_passed_as_a_query_parameter(self):
        client = MagicMock()
        client.post.return_value = HITS
        evalr.search_ids(client, "idx", {"size": 1}, "demo-hybrid-rerank")
        assert client.post.call_args.args[0] == "/idx/_search?search_pipeline=demo-hybrid-rerank"


class TestBootstrapCi:
    def test_constant_data_collapses_the_interval_to_the_constant(self):
        assert evalr.bootstrap_ci([0.5] * 50) == (0.5, 0.5)

    def test_the_seeded_interval_is_ordered_repeatable_and_brackets_the_mean(self):
        values = [0.1, 0.9, 0.4, 0.6, 0.5, 0.3, 0.7, 0.2, 0.8, 0.5]
        lo, hi = evalr.bootstrap_ci(values)
        assert lo <= statistics.fmean(values) <= hi
        assert evalr.bootstrap_ci(values) == (lo, hi)  # same seed, same interval

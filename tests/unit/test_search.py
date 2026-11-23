# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The inline v1 search used where there is no stored search template (Serverless)."""

from retail_agent_demo import search


def test_inline_v1_names_the_normalization_pipeline_and_keeps_the_filter_inside_the_hybrid():
    from unittest.mock import MagicMock
    client = MagicMock()
    client.post.return_value = {"hits": {"hits": [{"_id": "B1", "_score": 1.0, "_source": {"title": "Bank", "price": 9.5, "in_stock": True}}]}}
    out = search.run_v1_inline(client, "idx", "EMB", "dinosaur bank", None, 30, pipeline="hybrid-pipe")
    path, body = client.post.call_args.args
    assert path == "/idx/_search?search_pipeline=hybrid-pipe"
    assert body["query"]["hybrid"]["filter"] == {"range": {"price": {"gte": 0, "lte": 30}}}
    assert out[0].product_id == "B1"

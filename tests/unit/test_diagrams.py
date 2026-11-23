# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The architecture pictures in docs/images are built from the Graphviz sources in docs/diagrams."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAMES = ["architecture", "request-flow"]


@pytest.mark.parametrize("name", NAMES)
def test_every_picture_has_a_source_and_both_renderings(name):
    assert (ROOT / "docs/diagrams" / f"{name}.dot").read_text().strip().startswith("//")
    assert (ROOT / "docs/images" / f"{name}.png").stat().st_size > 5000
    assert (ROOT / "docs/images" / f"{name}.svg").read_text().lstrip().startswith("<?xml")


def test_the_pictures_name_the_services_the_stack_uses():
    text = (ROOT / "docs/diagrams/architecture.dot").read_text()
    for needle in ("OpenSearch Serverless", "OpenSearch Memory", "AgentCore", "Claude Haiku", "Titan", "Cohere Rerank"):
        assert needle in text

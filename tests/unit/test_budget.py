# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import pytest

from retail_agent_demo.budget import NO_LIMIT, parse_budget


@pytest.mark.parametrize("text,expected", [
    ("under 30 dollars", 30.0),
    ("something under $29.99", 29.99),
    ("my budget is 40 dollars now", 40.0),
    ("budget of $25.50", 25.50),
    ("less than 15", 15.0),
    ("maximum $100", 100.0),
])
def test_phrased_ceilings(text, expected):
    assert parse_budget(text) == (0.0, expected)


def test_a_stated_ceiling_beats_a_stray_dollar_amount():
    assert parse_budget("it has $5 shipping, my budget is 30 dollars") == (0.0, 30.0)


def test_falls_back_to_the_first_dollar_amount():
    assert parse_budget("about $35 would be fine") == (0.0, 35.0)


def test_no_amount_means_no_limit():
    assert parse_budget("a dinosaur piggy bank") == (0.0, NO_LIMIT)

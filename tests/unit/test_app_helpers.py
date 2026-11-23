# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from retail_agent_demo.app import md


def test_dollar_signs_are_escaped_so_two_prices_do_not_become_math():
    assert md("$24.99 and $27.99") == "\\$24.99 and \\$27.99"


def test_text_without_dollars_is_unchanged():
    assert md("no prices here") == "no prices here"


def test_dollars_the_model_already_escaped_are_not_escaped_twice():
    assert md("\\$24.99 and \\$27.99") == "\\$24.99 and \\$27.99"

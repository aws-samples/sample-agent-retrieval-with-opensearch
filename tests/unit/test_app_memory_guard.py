# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from retail_agent_demo.app import SESSION_KEYS, SHOPPER_KEYS, md


def test_the_cross_shopper_wipe_clears_exactly_the_keys_that_can_leak_between_shoppers():
    assert set(SESSION_KEYS) == {"agent_memory_id", "agent_memory_agent", "app_session_id", "recalled", "forgot"}
    # the saved outputs belong to one shopper too, but "New session" keeps them so earlier outputs stay comparable
    assert set(SHOPPER_KEYS) == set(SESSION_KEYS) | {"turns"} and "turns" not in SESSION_KEYS


def test_md_normalizes_a_mix_of_escaped_and_raw_dollar_signs():
    assert md("\\$30 and $40") == "\\$30 and \\$40"

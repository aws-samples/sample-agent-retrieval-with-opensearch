# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from retail_agent_demo import acts


def test_there_are_four_acts_in_order_and_every_step_says_what_it_shows():
    assert acts.acts() == [1, 2, 3, 4]
    assert [s.act for s in acts.STEPS] == sorted(s.act for s in acts.STEPS)
    assert all(s.says and s.look and s.title for s in acts.STEPS)


def test_position_counts_within_the_act_and_overall():
    assert acts.position(0) == (1, 1, 5, 1)
    assert acts.position(4) == (1, 5, 5, 5)
    assert acts.position(len(acts.STEPS) - 1)[0] == 4


def test_no_line_the_shopper_types_contains_a_person_s_name():
    for line in (acts.REQUEST, acts.REWRITE, acts.STOCK, acts.TODAY, acts.BUDGET, acts.FORGET_LINE):
        assert "Sam" not in line and "Bob" not in line


def test_the_ladder_steps_use_the_same_request_so_rank_movement_can_be_shown():
    ladder = [s for s in acts.STEPS if s.act == 1][:4]
    assert {s.query for s in ladder} == {acts.REWRITE}
    assert [s.version for s in ladder] == ["keyword", "v0", "v1", "v1+rerank"]  # lexical, then vector, hybrid, rerank


def test_the_demo_opens_at_step_3_but_keeps_steps_1_and_2_in_the_list():
    assert acts.START == acts.first() == 2 and len(acts.menu()) == len(acts.STEPS) == 11
    assert acts.menu()[0].startswith("1. ") and acts.menu()[2].startswith("3. ")
    assert acts.position(2) == (1, 3, 5, 3)  # step 3 of 5, overall 3 of 11
    assert "against vector" in acts.says(2) + acts.look(2)  # the full wording when nothing is said about what ran
    assert acts.look(2, {0, 1}) == acts.STEPS[2].look  # steps 1 and 2 were run: the full wording
    assert "against vector" not in acts.look(2, set()) and acts.says(2, set()) == acts.STEPS[2].alt_says  # opened at 3: no arrows to say
    assert acts.says(5, set()) == acts.STEPS[5].says  # a step with no alternative wording is unchanged


def test_scenario_b_turns_the_inventory_switch_on_and_the_last_step_opens_the_memory_lists():
    b = next(s for s in acts.STEPS if s.inventory)
    assert b.version == "v2" and b.query == acts.STOCK
    assert acts.STEPS[-1].lists and len(acts.STEPS) == 11
    assert all(s.shopper == "bob" for s in acts.STEPS)  # nothing in the guided demo can forget or switch a shopper

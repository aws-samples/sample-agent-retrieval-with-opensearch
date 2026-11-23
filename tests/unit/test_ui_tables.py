# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from retail_agent_demo import acts, ranks, turns, ui_tables


def test_a_highlighted_row_is_bold_with_a_light_green_background_in_every_cell():
    html = ui_tables.table(["a", "b"], [{"cells": ["x", "y"], "highlight": True}, {"cells": ["p", "q"]}])
    first, second = html.split("<tr>")[2:4]
    assert first.count("background:#d7f5d7") == 2 and first.count("font-weight:700") == 2
    assert "d7f5d7" not in second and "font-weight" not in second


def test_cells_are_escaped():
    html = ui_tables.table(["h<"], [{"cells": ["<b>x</b> & y"]}])
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt; &amp; y" in html and "h&lt;" in html


def test_the_typing_box_shows_the_text_so_far_and_a_caret_until_it_is_done():
    assert "dinosaur pi" in ui_tables.typing_box("dinosaur pi") and "▌" in ui_tables.typing_box("dinosaur pi")
    assert "▌" not in ui_tables.typing_box("dinosaur", caret=False) and "&lt;" in ui_tables.typing_box("<")


def turn(step, run, rows):
    return {"step": step, "run": run, "products": [{"id": i, "#": n, "product": f"title {i}"} for n, i in enumerate(rows, 1)]}


def test_the_comparison_grows_step_by_step_and_columns_follow_the_step_order():
    saved = [turn(0, "r", ["A", "B"]), turn(1, "r", ["B", "A", "C"])]
    columns, rows = ranks.comparison(saved, "r")
    assert columns == [acts.short(0), acts.short(1)]
    cells = {r["id"]: r["cells"] for r in rows}
    assert cells["A"] == ["#1", "#2"] and cells["B"] == ["#2", "#1"] and cells["C"] == ["–", "#3"]
    columns, _ = ranks.comparison(saved + [turn(2, "r", ["A"])], "r")
    assert len(columns) == 3  # one more column, the earlier ones kept


def test_a_step_that_ran_again_keeps_its_newest_result_and_another_run_is_left_out():
    saved = [turn(0, "r", ["A"]), turn(0, "r", ["B"]), turn(1, "other", ["Z"]), {"step": None, "run": None, "products": [{"id": "Q", "#": 1, "product": "q"}]}]
    columns, rows = ranks.comparison(saved, "r")
    assert columns == [acts.short(0)] and [r["id"] for r in rows] == ["B"]


def test_the_demo_product_is_always_in_the_comparison_even_when_it_ranks_low():
    many = [f"P{i}" for i in range(20)] + [acts.TARGET]
    _, rows = ranks.comparison([turn(0, "r", many)], "r", limit=12)
    assert len(rows) == 12 and rows[-1]["id"] == acts.TARGET


def test_every_step_has_a_short_column_name_and_every_version_a_distinct_explicit_label():
    assert len(acts.SHORT) == len(acts.STEPS) and len(set(acts.SHORT)) == len(acts.SHORT)
    assert len(set(turns.VERSION_LABELS.values())) == 7
    assert turns.VERSION_LABELS["keyword"] == "Lexical only" and "memory" in turns.VERSION_LABELS["v3"]
    assert "no memory" in turns.VERSION_LABELS["v2"] and "hybrid" in turns.VERSION_LABELS["v1"]


def test_older_labels_still_name_their_version_so_saved_results_keep_working():
    for old, kind in (("Keyword only", "keyword"), ("v0 — vector only", "v0"), ("v1 — hybrid", "v1"), ("v1+rerank", "v1+rerank"),
                      ("v2 — agent (tools)", "v2"), ("MCP — outside client", "mcp"), ("v3 — agent + memory", "v3")):
        assert turns.version_kind(old) == kind
    assert [turns.version_kind(label) for label in turns.VERSION_LABELS.values()] == list(turns.VERSION_LABELS)


def test_a_pressed_search_button_is_drawn_pushed_in_and_a_normal_one_is_not():
    assert "inset" not in ui_tables.typing_box("x") and "Search" in ui_tables.typing_box("x")
    assert "inset" in ui_tables.typing_box("x", caret=False, pressed=True)


def test_a_step_that_returned_nothing_still_has_a_column_and_is_listed_as_not_searching():
    saved = [turn(0, "r", ["A"]), {"step": 5, "run": "r", "products": []}]
    columns, rows = ranks.comparison(saved, "r")
    assert columns == [acts.short(0), acts.short(5)] and rows[0]["cells"] == ["#1", "–"]
    assert [name for name, _ in ranks.silent_steps(saved, "r")] == [acts.short(5)]

# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The on-disk copy of the saved outputs that lets a page refresh bring them back."""

import json
import os
import stat

from retail_agent_demo import turns
from retail_agent_demo.turn_store import TurnStore


def turn(n):
    return turns.new_turn(n, "v1 — hybrid", f"request {n}", "cfg", f"answer {n}")


def test_saved_outputs_come_back_in_order(tmp_path):
    store = TurnStore(tmp_path)
    store.save("domain", "bob", [turn(1), turn(2)])
    assert [t["prompt"] for t in store.load("domain", "bob")] == ["request 1", "request 2"]


def test_backends_and_shoppers_are_kept_apart(tmp_path):
    store = TurnStore(tmp_path)
    store.save("domain", "bob", [turn(1)])
    store.save("serverless", "bob", [turn(2)])
    store.save("domain", "demo-shopper", [turn(3)])
    assert store.load("domain", "bob")[0]["n"] == 1
    assert store.load("serverless", "bob")[0]["n"] == 2
    assert store.load("domain", "demo-shopper")[0]["n"] == 3
    assert store.load("domain", "nobody") == []


def test_only_the_newest_outputs_are_kept(tmp_path):
    store = TurnStore(tmp_path, limit=3)
    store.save("domain", "bob", [turn(i) for i in range(1, 8)])
    assert [t["n"] for t in store.load("domain", "bob")] == [5, 6, 7]


def test_clear_removes_the_copy_and_is_safe_to_repeat(tmp_path):
    store = TurnStore(tmp_path)
    store.save("domain", "bob", [turn(1)])
    store.clear("domain", "bob")
    store.clear("domain", "bob")
    assert store.load("domain", "bob") == []


def test_a_damaged_or_foreign_file_is_treated_as_empty_not_as_a_crash(tmp_path):
    store = TurnStore(tmp_path)
    path = store._path("domain", "bob")
    for junk in ("", "{not json", '{"a": 1}', json.dumps([{"n": 1}, "text", 3])):
        path.write_text(junk)
        assert store.load("domain", "bob") == []


def test_a_shopper_name_cannot_escape_the_state_directory(tmp_path):
    store = TurnStore(tmp_path / "state")
    store.save("domain", "../../etc/passwd", [turn(1)])
    files = [p for p in (tmp_path / "state").iterdir()]
    assert len(files) == 1 and files[0].parent == tmp_path / "state" and "/" not in files[0].name
    assert not (tmp_path / "etc").exists()


def test_the_state_directory_is_private_and_no_temp_file_is_left_behind(tmp_path):
    store = TurnStore(tmp_path / "state")
    store.save("domain", "bob", [turn(1)])
    assert stat.S_IMODE(os.stat(tmp_path / "state").st_mode) == 0o700
    assert [p.name for p in (tmp_path / "state").iterdir() if p.name.endswith(".tmp")] == []

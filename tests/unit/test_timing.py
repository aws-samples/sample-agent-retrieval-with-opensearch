# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import time

import pytest

from retail_agent_demo import agentcore as ac
from retail_agent_demo import mcp_client as m
from retail_agent_demo import osmemory as osm
from retail_agent_demo import timing, turns


def test_a_span_is_recorded_even_when_the_block_fails():
    tl = timing.Timeline()
    with pytest.raises(ValueError), tl.span("boom"):
        raise ValueError
    assert tl.segments[0]["name"] == "boom" and tl.segments[0]["level"] == 0


def test_finish_reports_the_total_and_adds_the_page_work_between_steps(monkeypatch):
    tl = timing.Timeline()
    tl.add("search", 0.02)
    tl.add("part of search", 0.01, level=1)  # a part of a step is not counted twice
    time.sleep(0.12)
    out = tl.finish()
    assert out["total"] >= 0.12 and out["segments"][-1]["name"] == "Page work between the steps"
    assert out["segments"][-1]["seconds"] == pytest.approx(out["total"] - 0.02, abs=0.01)


def test_no_remainder_row_when_the_steps_cover_the_total():
    tl = timing.Timeline()
    tl.start -= 1.0
    tl.add("everything", 1.0)
    assert [s["name"] for s in tl.finish()["segments"]] == ["everything"]


@pytest.mark.parametrize("seconds,text", [(0.4237, "0.42 s"), (3.21, "3.2 s"), (65, "1 min 05 s")])
def test_seconds_text(seconds, text):
    assert timing.seconds_text(seconds) == text


def test_the_steps_of_an_agent_run_are_the_gaps_between_its_trace_records():
    trail = [{"metadata": {"type": "message"}, "created_time": 1000},
             {"metadata": {"type": "trace", "origin": "LLM", "trace_number": 1}, "created_time": 3700},
             {"metadata": {"type": "trace", "origin": "plan_product_query", "trace_number": 2}, "created_time": 7300},
             {"metadata": {"type": "trace", "origin": "inventory___check_stock", "trace_number": 3}, "created_time": 7500}]
    segs = timing.trail_segments(trail)
    assert [(s["name"], s["seconds"]) for s in segs] == [("Model turn", 2.7), ("plan_product_query", 3.6), ("check_stock", 0.2)]
    assert all(s["level"] == 1 for s in segs)
    assert timing.trail_segments([{"metadata": {"type": "message"}, "created_time": 1}]) == []


def test_runtime_timings_come_from_the_event_loop_summary():
    summary = {"traces": [{"name": "Cycle 1", "children": [{"name": "stream_messages", "duration": 2.4},
                                                          {"name": "Tool: run_product_search", "duration": 0.31}]},
                         {"name": "Cycle 2", "children": [{"name": "Recursive call", "duration": 9.9},
                                                          {"name": "stream_messages", "duration": 1.1},
                                                          {"name": "Tool: inventory___check_stock", "duration": None}]}]}
    assert ac.timings_from_metrics(summary) == [{"name": "Model turn", "seconds": 2.4}, {"name": "run_product_search", "seconds": 0.31},
                                                {"name": "Model turn", "seconds": 1.1}]
    assert ac.timings_from_metrics({}) == [] and ac.timings_from_metrics(None) == []
    assert timing.runtime_segments([{"name": "Model turn", "seconds": 2.4}])[0]["level"] == 1


def test_mcp_steps_carry_their_seconds_into_the_timeline():
    class Fake:
        def post(self, path, body):
            time.sleep(0.02)
            return {"result": {"serverInfo": {"name": "s"}, "protocolVersion": "1"}}
    s = m.McpSession(Fake())
    s.initialize()
    segs = timing.mcp_segments(s.trace.steps)
    assert segs[0]["name"] == "MCP initialize" and segs[0]["seconds"] >= 0.02


def test_the_fold_title_names_the_time_and_older_results_without_timing_still_have_a_title():
    turn = turns.new_turn(1, "Lexical only", "q", "cfg", "a", timing={"total": 3.24, "segments": []})
    assert "took 3.2 s" in turns.fold_label(turn)
    old = {**turn, "timing": None}
    assert "took" not in turns.fold_label(old)
    del old["timing"]
    assert "took" not in turns.fold_label(old)


def test_the_bar_is_proportional_and_never_empty_for_a_real_step():
    assert timing.bar(5, 10, 10) == "█" * 5 and timing.bar(0.01, 10, 10) == "█" and timing.bar(0, 10) == "" and timing.bar(1, 0) == ""


def test_forget_repeats_until_no_memories_or_events_are_left(monkeypatch):
    calls = {"forget": 0}

    def fake_forget(client, space_id, actor):
        calls["forget"] += 1
        return {"memories": 1, "events": 1, "sessions": 1}

    left = iter([[{"memory": "x"}], []])  # facts: one left after the first delete, none after the second
    monkeypatch.setattr(osm, "forget_shopper", fake_forget)
    monkeypatch.setattr(osm, "list_facts", lambda c, m, u: next(left))
    monkeypatch.setattr(osm, "_events", lambda c, m, u: [])
    out = osm.forget_shopper_verified(object(), "sp", "demo-shopper", sleep=lambda s: None)
    assert calls["forget"] == 2 and out["left over"] == 0 and out["memories"] == 2


def test_the_hosted_agent_is_warmed_at_most_once_in_a_few_minutes_and_a_failure_is_not_an_error():
    sent = []

    class Body:
        def read(self):
            return b'{"warm": true, "seconds": 7.5}'

    class Client:
        def invoke_agent_runtime(self, **kw):
            sent.append(kw)
            return {"response": Body()}

    ac._WARM["at"] = 0.0
    clock = iter([1000.0, 1000.0, 1100.0, 1300.0, 1300.0, 1300.0])  # call 1, call 2 (100 s later), call 3 (past the interval)
    now = lambda: next(clock)  # noqa: E731
    assert ac.warm_up(Client(), "arn", now=now) == 7.5
    assert ac.warm_up(Client(), "arn", now=now) is None  # warmed 100 s ago
    assert ac.warm_up(Client(), "arn", now=now) == 7.5
    assert len(sent) == 2 and b'"warmup": true' in sent[0]["payload"]

    class Broken:
        def invoke_agent_runtime(self, **kw):
            raise RuntimeError("down")
    ac._WARM["at"] = 0.0
    assert ac.warm_up(Broken(), "arn", now=lambda: 5000.0) is None and ac._WARM["at"] == 0.0  # it will try again next time

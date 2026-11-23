# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""How long a query took, in total and by step, for the fold of each result.

A `Timeline` collects named segments (`level` 0 = a step of the request, 1 = a part of the step above it). The app starts one per
request, wraps each step in `span()`, and `finish()` adds the remainder (page work between the steps) so the segments account for
the total. Pure functions otherwise; no Streamlit.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

MIN_REMAINDER = 0.05  # seconds: a smaller gap is not worth a row


class Timeline:
    def __init__(self) -> None:
        self.start = time.perf_counter()
        self.segments: list[dict] = []

    @contextmanager
    def span(self, name: str, level: int = 0):
        """Time the block; the segment is kept even when the block raises."""
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, time.perf_counter() - t0, level)

    def add(self, name: str, seconds: float, level: int = 0) -> None:
        self.segments.append({"name": name, "seconds": round(max(float(seconds), 0.0), 3), "level": level})

    def finish(self) -> dict:
        total = round(time.perf_counter() - self.start, 3)
        counted = sum(s["seconds"] for s in self.segments if s["level"] == 0)
        if total - counted >= MIN_REMAINDER:
            self.add("Page work between the steps", total - counted)
        return {"total": total, "segments": list(self.segments)}


def seconds_text(seconds: float) -> str:
    """'0.42 s', '3.2 s', '1 min 05 s' for display."""
    if seconds < 1:
        return f"{seconds:.2f} s"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min {rest:02d} s"


def trail_segments(trail: list[dict]) -> list[dict]:
    """The steps of one conversational-agent run from its working-memory records, as [{'name', 'seconds', 'level'}].

    Each trace record is written when its step ends, so a step's length is the gap to the record before it; the first step is measured
    from the run's message record (both timestamps come from the domain's clock, so the app's own clock does not matter). A tool trace
    is a tool call; an 'LLM' trace is one model turn."""
    traces = [t for t in trail if (t.get("metadata") or {}).get("type") == "trace" and t.get("created_time")]
    if not traces:
        return []
    traces.sort(key=lambda t: int((t.get("metadata") or {}).get("trace_number") or 0))
    messages = [t["created_time"] for t in trail if (t.get("metadata") or {}).get("type") == "message" and t.get("created_time")]
    previous = min(messages) if messages else traces[0]["created_time"]
    out = []
    for t in traces:
        origin = (t.get("metadata") or {}).get("origin") or "step"
        name = "Model turn" if origin == "LLM" else origin.split("___", 1)[-1]
        out.append({"name": name, "seconds": round(max(t["created_time"] - previous, 0) / 1000.0, 3), "level": 1})
        previous = t["created_time"]
    return out


def runtime_segments(timings: list[dict] | None) -> list[dict]:
    """The AgentCore runtime reports its model turns and tool calls as [{'name', 'seconds'}]; same shape, one level down."""
    return [{"name": str(t.get("name") or "step"), "seconds": round(float(t.get("seconds") or 0), 3), "level": 1} for t in timings or []]


def mcp_segments(trace: list[dict]) -> list[dict]:
    return [{"name": f"MCP {t['method']}", "seconds": round(float(t["seconds"]), 3), "level": 0} for t in trace if "seconds" in t]


def bar(seconds: float, total: float, width: int = 18) -> str:
    if total <= 0:
        return ""
    return "█" * max(1 if seconds > 0 else 0, round(width * min(seconds / total, 1.0)))

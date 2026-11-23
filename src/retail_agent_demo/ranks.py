# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Rank movement between retrieval versions, derived from the saved outputs (so it survives a page refresh).

The ladder is keyword -> vector -> hybrid -> hybrid + rerank. For the same request text, each rung is compared with the
nearest lower rung that has results, and the page shows where every product moved. Pure functions, unit-tested."""

from __future__ import annotations

from . import acts
from .turns import version_kind

LADDER = ("keyword", "v0", "v1", "v1+rerank")
NAMES = {"keyword": "Lexical", "v0": "Vector", "v1": "Lexical + vector", "v1+rerank": "+ Rerank"}


def same_request(a: str, b: str) -> bool:
    return " ".join(str(a).lower().split()) == " ".join(str(b).lower().split())


def results_by_rung(turns: list[dict], query: str) -> dict[str, list[dict]]:
    """The newest saved result of each ladder rung for this request: {kind: [row, ...]} (rows carry 'id' and 'product')."""
    found: dict[str, list[dict]] = {}
    for turn in turns:  # oldest first, so a later run of a rung replaces an earlier one
        kind = version_kind(turn["version"])
        if kind in LADDER and same_request(turn["prompt"], query) and turn.get("products"):
            found[kind] = turn["products"]
    return found


def movement(turns: list[dict], kind: str, query: str, current_ids: list[str]) -> dict[str, str]:
    """For each current product: 'new', '▲n', '▼n' or '=', against the nearest lower rung with results ({} if none)."""
    if kind not in LADDER:
        return {}
    before = results_by_rung(turns, query)
    for lower in reversed(LADDER[: LADDER.index(kind)]):
        if lower in before:
            previous = [r.get("id") for r in before[lower]]
            break
    else:
        return {}
    moves = {}
    for rank, pid in enumerate(current_ids, 1):
        if pid not in previous:
            moves[pid] = "new"
        else:
            was = previous.index(pid) + 1
            moves[pid] = f"▲{was - rank}" if was > rank else (f"▼{rank - was}" if was < rank else "=")
    return moves


def ladder_table(turns: list[dict], query: str, limit: int = 6) -> tuple[list[str], list[dict]]:
    """The rungs run so far for this request and, per product, its position on each: ([kinds], [{'id','title','pos'}])."""
    found = results_by_rung(turns, query)
    kinds = [k for k in LADDER if k in found]
    seen: dict[str, str] = {}
    for kind in reversed(kinds):  # the highest rung first: its order decides the row order
        for row in found[kind]:
            seen.setdefault(row.get("id"), row.get("product", ""))
    rows = []
    for pid, title in seen.items():
        pos = {k: next((i for i, r in enumerate(found[k], 1) if r.get("id") == pid), None) for k in kinds}
        rows.append({"id": pid, "title": title, "pos": pos})
    top = kinds[-1] if kinds else None  # rows follow the order of the highest rung run; products that left it come last
    rows.sort(key=lambda r: (r["pos"][top] or 99, min((p for p in r["pos"].values() if p), default=99)))
    return kinds, rows[:limit]


def comparison(turns: list[dict], run: object, limit: int = 12) -> tuple[list[str], list[dict]]:
    """What each guided step returned, side by side: ([column names], [{'id','title','cells'}]).

    Columns are the steps of this guided run in step order (a step that ran again keeps its newest result); rows are the products
    any step returned, best position first, with the demo's target product always kept; a cell is '#3' or '–'."""
    by_step: dict[int, dict] = {}
    for turn in turns:
        if turn.get("step") is not None and turn.get("run") == run:  # a step that returned nothing still gets its column
            by_step[turn["step"]] = turn
    steps = sorted(by_step)
    columns = [acts.short(i) for i in steps]
    positions: dict[str, dict[int, int]] = {}
    titles: dict[str, str] = {}
    for i in steps:
        for row in by_step[i].get("products") or []:
            positions.setdefault(row["id"], {})[i] = row["#"]
            titles.setdefault(row["id"], row["product"])
    order = sorted(positions, key=lambda pid: (min(positions[pid].values()), -len(positions[pid])))
    keep = order[:limit]
    if acts.TARGET in positions and acts.TARGET not in keep:
        keep = keep[: limit - 1] + [acts.TARGET]
    rows = [{"id": pid, "title": titles[pid],
             "cells": [f"#{positions[pid][i]}" if i in positions[pid] else "–" for i in steps]} for pid in keep]
    return columns, rows


def silent_steps(turns: list[dict], run: object) -> list[tuple[str, dict]]:
    """The guided steps of this run whose answer came without a search (an agent that did not call its tool), with their turn."""
    last: dict[int, dict] = {}
    for turn in turns:
        if turn.get("step") is not None and turn.get("run") == run:
            last[turn["step"]] = turn
    return [(acts.short(i), last[i]) for i in sorted(last) if not last[i].get("products")]

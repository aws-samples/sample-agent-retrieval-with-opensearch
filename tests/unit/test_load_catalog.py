# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lc = load_module("load_catalog", ROOT / "scripts" / "load_catalog.py")


class TestPacer:
    def test_throttling_grows_the_delay_by_half(self):
        assert lc.Pacer(start=0.4).update(True) == pytest.approx(0.6)

    def test_growth_is_capped_at_the_ceiling(self):
        pacer = lc.Pacer(start=8.0, ceiling=10.0)
        assert pacer.update(True) == 10.0
        assert pacer.update(True) == 10.0

    def test_success_decays_the_delay_by_five_percent(self):
        assert lc.Pacer(start=1.0).update(False) == pytest.approx(0.95)

    def test_decay_never_goes_below_the_floor(self):
        pacer = lc.Pacer(start=0.1, floor=0.1)
        assert pacer.update(False) == 0.1


class FakeClient:
    """Answers /_bulk with a status per product; records which products were sent."""

    def __init__(self, statuses: dict[str, list[int]]):
        self.statuses = statuses  # product_id -> status per attempt (last repeats)
        self.sent: list[list[str]] = []

    def _status(self, pid: str) -> int:
        seq = self.statuses[pid]
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def post(self, path, body=None, timeout=None, content_type=None):
        if path.startswith("/_bulk"):  # body is the chunk itself: bulk_body is stubbed to identity below
            self.sent.append([r["product_id"] for r in body])
            return {"items": [{"index": {"_id": r["product_id"], "status": self._status(r["product_id"])}}
                              for r in body]}
        return {}

    def get(self, path):
        return {"count": 0}


def run_main(monkeypatch, tmp_path, statuses, product_ids):
    done_file = tmp_path / "done.txt"
    client = FakeClient(statuses)
    settings = MagicMock()
    settings.id = lambda key: {"products_index": "idx", "ingest_pipeline": "pipe"}[key]
    monkeypatch.setattr(lc, "load_settings", lambda: settings)
    monkeypatch.setattr(lc, "OSClient", lambda s, timeout=None: client)
    monkeypatch.setattr(lc.pd, "read_parquet", lambda path: None)
    monkeypatch.setattr(lc, "select_products", lambda e, p: pd.DataFrame({"product_id": product_ids}))
    monkeypatch.setattr(lc, "bulk_body", lambda chunk, index, with_embed_text=False, image_vectors=None: chunk)
    monkeypatch.setattr(lc.boto3, "client", lambda *a, **k: object())
    monkeypatch.setattr(lc.images, "picture_vectors", lambda runtime, ids: {})
    monkeypatch.setattr(lc.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv",
                        ["load_catalog.py", "--done", str(done_file), "--batch", "1", "--workers", "1"])
    lc.main()
    return client, done_file


def test_products_already_in_the_done_file_are_never_sent_again(monkeypatch, tmp_path):
    (tmp_path / "done.txt").write_text("p0\n")
    client, _ = run_main(monkeypatch, tmp_path, {"p1": [201], "p2": [200]}, ["p0", "p1", "p2"])
    sent = [pid for chunk in client.sent for pid in chunk]
    assert "p0" not in sent and set(sent) == {"p1", "p2"}


def test_only_indexed_products_are_recorded_as_done_and_throttled_ones_are_retried(monkeypatch, tmp_path):
    client, done_file = run_main(monkeypatch, tmp_path,
                                 {"p1": [201], "p2": [429, 200], "p3": [200]}, ["p1", "p2", "p3"])
    # p2 was throttled on the first pass, so it is not marked done and the next pass sends it again
    assert [pid for chunk in client.sent for pid in chunk].count("p2") == 2
    assert set(done_file.read_text().split()) == {"p1", "p2", "p3"}

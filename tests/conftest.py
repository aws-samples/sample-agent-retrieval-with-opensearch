# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_app_state(tmp_path, monkeypatch):
    """The app keeps its saved outputs on disk. No test may write to (or read) a developer's real state directory."""
    monkeypatch.setenv("APP_STATE_DIR", str(tmp_path / "app-state"))
    monkeypatch.setenv("APP_TYPING_SECONDS", "0")  # no typing animation in tests

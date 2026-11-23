# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Keeps the demo app's saved outputs on disk, per backend and shopper, so a browser refresh does not wipe them.

Streamlit forgets everything in a tab's session when the page reloads. The outputs (plain dictionaries, see turns.py) are
therefore written to a small JSON file after every turn and read back when a session starts. One file per backend
("domain" or "serverless") and shopper; the newest `limit` outputs are kept. Clearing deletes the file.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path

REQUIRED = {"n", "version", "prompt", "config", "answer", "products", "steps", "trace", "notes"}


def default_dir() -> Path:
    return Path(os.environ.get("APP_STATE_DIR") or Path(__file__).resolve().parents[2] / ".state")


class TurnStore:
    def __init__(self, directory: Path | str | None = None, limit: int = 60):
        self.dir = Path(directory) if directory else default_dir()
        self.limit = limit

    def _path(self, backend: str, user: str) -> Path:
        safe = re.sub(r"[^a-z0-9_-]", "_", f"{backend}-{user}".lower())
        return self.dir / f"turns-{safe}.json"

    def load(self, backend: str, user: str) -> list[dict]:
        """The saved outputs, oldest first. A missing, unreadable or malformed file is treated as empty."""
        try:
            data = json.loads(self._path(backend, user).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [t for t in data if isinstance(t, dict) and REQUIRED <= t.keys()]

    def save(self, backend: str, user: str, turns: list[dict]) -> None:
        """Write the newest outputs atomically (a reader never sees half a file)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, stat.S_IRWXU)  # owner only
        path = self._path(backend, user)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(turns[-self.limit:], f)
            os.replace(tmp, path)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def clear(self, backend: str, user: str) -> None:
        self._path(backend, user).unlink(missing_ok=True)

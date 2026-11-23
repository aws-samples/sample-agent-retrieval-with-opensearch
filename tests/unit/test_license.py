# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The project is Apache-2.0 everywhere: the LICENSE text, the file headers, NOTICE, pyproject and the contribution terms."""

import re
import subprocess  # nosec B404 - imported for constants or in the single audited helper
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEADER_SUFFIXES = {".py", ".sh", ".yaml", ".yml"}
SKIP = {".venv", "node_modules", ".git", "build", "dist"}


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")  # nosec B603 B607 - fixed arguments in a test of the repository; git from PATH in a test of the repository
        return [ROOT / p for p in out if p and (ROOT / p).is_file()]
    except (OSError, subprocess.CalledProcessError):  # not a git checkout (an sdist): walk the tree
        return [p for p in ROOT.rglob("*") if p.is_file() and not SKIP & set(p.relative_to(ROOT).parts)]


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def test_the_license_file_is_the_apache_2_license_text():
    body = text(ROOT / "LICENSE")
    assert "Apache License" in body and "Version 2.0, January 2004" in body
    assert "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION" in body
    assert "MIT No Attribution" not in body


def test_every_source_and_config_file_has_the_apache_header_with_the_copyright_holder():
    missing = []
    for path in tracked_files():
        if path.suffix in HEADER_SUFFIXES:
            head = "\n".join(text(path).splitlines()[:6])
            if "Copyright Amazon.com, Inc. or its affiliates" not in head or "SPDX-License-Identifier: Apache-2.0" not in head:
                missing.append(str(path.relative_to(ROOT)))
    assert not missing, f"files without the Apache-2.0 header in their first lines: {missing}"


def test_no_file_still_declares_mit_0():
    this = Path(__file__).resolve()
    hits = [str(p.relative_to(ROOT)) for p in tracked_files()
            if p.suffix in HEADER_SUFFIXES | {".md", ".toml", ".json", ""} and p.resolve() != this
            and re.search(r"MIT-0|MIT No Attribution", text(p))]
    # the dependency table names MIT-0 as another package's licence
    allowed = {"docs/dependency-licenses.md"}
    assert not [h for h in hits if h not in allowed], hits


def test_notice_pyproject_readme_and_contributing_agree():
    assert "Apache License, Version 2.0" in text(ROOT / "NOTICE")
    assert 'license = { text = "Apache-2.0" }' in text(ROOT / "pyproject.toml")
    assert "Apache-2.0" in text(ROOT / "README.md").split("## License")[1]
    assert "[LICENSE](LICENSE)" in text(ROOT / "CONTRIBUTING.md")  # the shared contributing guide points at the license file

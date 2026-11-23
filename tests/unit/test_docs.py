# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The documentation matches the repository: links and cited files exist, and nothing personal or internal is committed."""

import fnmatch
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


SKIP_DIRS = {".ash", "talk", ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".state", "data", "node_modules", "build", "dist"}
SKIP_FILES = {".env", "ids.json"}  # local files that git ignores
BINARY = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".pptx", ".mp4", ".webm", ".mov", ".zip", ".parquet", ".pyc", ".html")


def tracked(*patterns):
    """The repository's files (the ones git would track): a walk that leaves out caches, virtual environments and local files."""
    found = []
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in files:
            local_ids = name.startswith("ids") and name.endswith(".json") and name != "ids.example.json"
            if name in SKIP_FILES or local_ids or (name.startswith(".env") and name != ".env.example") or name.endswith(BINARY):
                continue
            rel = os.path.relpath(os.path.join(folder, name), ROOT)
            if os.path.getsize(os.path.join(folder, name)) > 2_000_000:  # nothing in the repository's text is this large
                continue
            if not patterns or any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(name, p) for p in patterns):
                found.append(rel)
    return sorted(found)


def test_every_relative_markdown_link_resolves():
    broken = []
    for name in tracked("*.md"):
        text = (ROOT / name).read_text()
        for link in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
            if link.startswith(("http://", "https://", "mailto:")):
                continue
            if not (ROOT / name).parent.joinpath(link).resolve().exists():
                broken.append((name, link))
    assert not broken, broken


def test_every_repository_file_named_in_backticks_exists():
    broken = []
    for name in tracked("*.md"):
        for path in re.findall(r"`((?:docs|scripts|src|tests|infra|config)/[A-Za-z0-9_./-]+\.[a-z]+)`", (ROOT / name).read_text()):
            if "*" in path or path in ("config/ids.json",):  # ids.json is written by the deploy scripts
                continue
            if not (ROOT / path).exists():
                broken.append((name, path))
    assert not broken, broken


def test_no_personal_or_internal_names_are_committed():
    # Built from pieces so that this file does not contain the words it forbids.
    forbidden = [re.compile(p, re.I) for p in (
        "vayn" + "sh", "mikh" + "ail", "mut" + "hu", "pitchai" + "mani", "chalk" + " talk", "re:" + "invent", "guard" + "ian",
        "pc" + "sr", "isen" + "gard", "mid" + "way", r"w\.ama" + "zon", "a2" + "z", r"\bpresen" + "ter",
    )]
    hits = []
    for name in tracked():
        if name.startswith("assets/") or name.endswith((".png", ".jpg", ".svg")) or name == "docs/threat-model.tc.json":
            continue
        text = (ROOT / name).read_text(errors="ignore")
        for rx in forbidden:
            if rx.search(text):
                hits.append((name, rx.pattern))
    assert not hits, hits


def test_the_only_email_addresses_are_the_standard_amazon_contacts():
    found = set()
    for name in tracked("*.md", "*.py", "*.yaml", "*.toml", "*.sh", "*.json"):
        if name == "docs/threat-model.tc.json":
            continue
        found |= set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}", (ROOT / name).read_text(errors="ignore")))
    assert found <= {"aws-security@amazon.com", "opensource-codeofconduct@amazon.com", "noreply@example.com"}, found


def test_the_guided_demo_document_lists_every_step_and_its_typed_line():
    from retail_agent_demo import acts

    text = (ROOT / "docs/guided-demo.md").read_text()
    for i, step in enumerate(acts.STEPS, 1):
        assert f"### Step {i}. {step.title}" in text, step.title
        assert step.look in text, f"step {i}: what to look at"
        if step.query:
            assert step.query in text, f"step {i}: typed line"


def test_the_documentation_index_lists_every_document():
    index = (ROOT / "docs/README.md").read_text()
    for name in tracked("docs/*.md"):
        base = os.path.basename(name)
        if os.path.dirname(name) == "docs" and base != "README.md":
            assert f"]({base})" in index, base

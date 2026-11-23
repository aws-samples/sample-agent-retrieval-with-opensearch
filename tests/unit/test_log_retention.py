# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Every CloudWatch log group the templates create keeps its logs at least 90 days."""

from pathlib import Path

import pytest
from cfnlint.decode import cfn_yaml

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = sorted((ROOT / "infra").rglob("*.yaml"))


def log_groups(template):
    return {n: r for n, r in (template.get("Resources") or {}).items() if r.get("Type") == "AWS::Logs::LogGroup"}


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_every_log_group_has_a_literal_retention_of_at_least_90_days(path):
    template = cfn_yaml.load(str(path))
    for name, resource in log_groups(template).items():
        days = resource["Properties"].get("RetentionInDays")
        assert isinstance(days, int) and days >= 90, f"{path.name}:{name} keeps {days!r} days"


def test_the_templates_create_at_least_two_log_groups():
    assert sum(len(log_groups(cfn_yaml.load(str(p)))) for p in TEMPLATES) >= 2


def test_the_runtime_takes_the_session_id_from_the_runtime_context_first():
    main = (ROOT / "infra/serverless/agentcore/main.py").read_text()
    assert 'getattr(context, "session_id", None) or payload.get("session_id")' in main


def test_the_threat_composer_export_matches_the_markdown_model():
    import importlib.util

    spec = importlib.util.spec_from_file_location("tm", ROOT / "scripts/threat_model_to_tc.py")
    tm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tm)
    import json

    assert json.loads((ROOT / "docs/threat-model.tc.json").read_text()) == tm.build((ROOT / "docs/threat-model.md").read_text())

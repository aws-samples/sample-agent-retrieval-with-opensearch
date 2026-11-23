# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The runtime role in infra/serverless/agentcore.yaml: where `Resource: "*"` is allowed, and that the rest is scoped (threat model row 10)."""

from pathlib import Path

import pytest
from cfnlint.decode.cfn_yaml import load

TEMPLATE = Path(__file__).resolve().parents[2] / "infra/serverless/agentcore.yaml"


@pytest.fixture(scope="module")
def runtime_statements():
    """Every statement of the runtime role's permissions: the managed policies attached through ManagedPolicyArns (Holmes: no inline policies)."""
    template = load(str(TEMPLATE))
    resources = template["Resources"]
    role = resources["RuntimeRole"]["Properties"]
    assert not role.get("Policies"), "the runtime role must not carry inline policies"
    out = {}
    for arn in role["ManagedPolicyArns"]:
        ref = arn["Fn::If"][1] if "Fn::If" in arn else arn
        for st in resources[ref["Ref"]]["Properties"]["PolicyDocument"]["Statement"]:
            if "Sid" in st:
                out[st["Sid"]] = st
    return out


def as_list(x):
    return x if isinstance(x, list) else [x]


def test_a_wildcard_resource_stays_only_on_xray_writes_and_the_namespaced_metric_write(runtime_statements):
    wild = {sid for sid, st in runtime_statements.items() if "*" in as_list(st["Resource"])}
    assert wild == {"Traces", "Metrics"}
    assert set(as_list(runtime_statements["Traces"]["Action"])) == {"xray:PutTraceSegments", "xray:PutTelemetryRecords"}


def test_putmetricdata_is_limited_to_the_agentcore_namespace(runtime_statements):
    st = runtime_statements["Metrics"]
    assert as_list(st["Action"]) == ["cloudwatch:PutMetricData"]
    assert st["Condition"] == {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}


def test_workload_access_tokens_are_scoped_to_this_runtimes_identity(runtime_statements):
    st = runtime_statements["WorkloadIdentity"]
    assert set(as_list(st["Action"])) == {"bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForUserId"}
    resources = [r["Fn::Sub"] if isinstance(r, dict) else r for r in as_list(st["Resource"])]
    assert len(resources) == 2 and all("*" not in r.replace("sample_agent_ret_shopping_agent-*", "") for r in resources)
    assert any(r.endswith("workload-identity-directory/default") for r in resources)
    assert any(r.endswith("workload-identity/sample_agent_ret_shopping_agent-*") for r in resources)


# ---- the first, text-level checks (kept) ---------------------------------------------------------------------------
TEXT = TEMPLATE.read_text()


def block(sid: str) -> str:
    start = TEXT.index(f"- Sid: {sid}")
    return TEXT[start:TEXT.index("- Sid:", start + 10) if "- Sid:" in TEXT[start + 10:] else len(TEXT)]


def test_workload_access_tokens_are_scoped_to_the_runtimes_identities():
    b = block("WorkloadIdentity")
    assert 'Resource: "*"' not in b
    assert "workload-identity-directory/default" in b
    assert "workload-identity/sample_agent_ret_shopping_agent-*" in b


def test_no_statement_grants_agentcore_actions_on_everything():
    for line_no, line in enumerate(TEXT.splitlines()):
        if "bedrock-agentcore:" in line and "Action" in line:
            window = "\n".join(TEXT.splitlines()[line_no:line_no + 8])
            assert 'Resource: "*"' not in window or "xray" in window or "cloudwatch" in window, line

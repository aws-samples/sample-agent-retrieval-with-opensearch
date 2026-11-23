# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The threat model checked against the repository.

One test per claim: the code controls the model cites exist, and docs/threat-model.md says what the code does. If a control
is removed, or the model drifts from the code, a test here fails.
"""

import re
from pathlib import Path

import pytest
from cfnlint.decode.cfn_yaml import load

ROOT = Path(__file__).resolve().parents[2]
MODEL = (ROOT / "docs/threat-model.md").read_text()
README = (ROOT / "README.md").read_text()
AGENTCORE = ROOT / "infra/serverless/agentcore.yaml"


def row(label: str) -> str:
    """The text of one table row of the threat model, by its first cell (for example '10' or 'D1')."""
    for line in MODEL.splitlines():
        if re.match(rf"^\| {re.escape(label)} \|", line):
            return line
    raise AssertionError(f"threat-model.md has no row {label}")


def as_list(x):
    return x if isinstance(x, list) else [x]


# ---- 1. Row 10: the wildcard that can be scoped ------------------------------------------------------------------------------
def runtime_statements():
    t = load(str(AGENTCORE))
    res = t["Resources"]
    out = {}
    for arn in res["RuntimeRole"]["Properties"]["ManagedPolicyArns"]:
        ref = arn["Fn::If"][1] if "Fn::If" in arn else arn
        for st in res[ref["Ref"]]["Properties"]["PolicyDocument"]["Statement"]:
            out[st.get("Sid", "?")] = st
    return out, t


def test_1_workload_tokens_are_scoped_and_metrics_carry_a_namespace_condition():
    st, _ = runtime_statements()
    resources = [r["Fn::Sub"] if isinstance(r, dict) else r for r in as_list(st["WorkloadIdentity"]["Resource"])]
    assert len(resources) == 2 and all("workload-identity-directory/default" in r for r in resources)
    assert st["Metrics"]["Condition"] == {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}
    assert set(as_list(st["Traces"]["Action"])) == {"xray:PutTraceSegments", "xray:PutTelemetryRecords"}
    assert "Telemetry" not in st  # PutMetricData is no longer in the same wildcard statement as the X-Ray writes


def test_1_the_cfn_nag_reason_no_longer_claims_workload_tokens_cannot_be_scoped():
    text = AGENTCORE.read_text()
    reasons = re.findall(r"reason: >-\n((?:\s+.*\n)+)", text)
    assert any("scoped to this runtime's identity" in r for r in reasons)
    assert "AgentCore workload access tokens" not in "".join(reasons)


def test_1_row_10_residual_says_what_remains_a_wildcard_and_why():
    r = row("10")
    assert "X-Ray trace writes" in r and "bedrock:Rerank" in r and "limited by namespace" in r
    assert "Workload access tokens are scoped to this runtime's workload identity" in r
    assert "claude-haiku-4-5-*" in r and "cross-Region inference profiles" in r


# ---- 2. Supply chain and deployment ------------------------------------------------------------------------------------------
def test_2_runtime_dependencies_are_pinned_and_installed_as_wheels_only():
    reqs = [ln for ln in (ROOT / "infra/serverless/agentcore/requirements.txt").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    assert reqs and all("==" in r for r in reqs)
    deploy = (ROOT / "infra/serverless/deploy_agentcore.py").read_text()
    assert "--only-binary=:all:" in deploy and "AGENT_REQUIREMENTS" in deploy


def test_2_the_code_bucket_is_owner_checked_tls_only_and_never_written_to_when_foreign():
    deploy = (ROOT / "infra/serverless/deploy_agentcore.py").read_text()
    assert "head_bucket(Bucket=name, ExpectedBucketOwner=account)" in deploy
    assert deploy.count("ExpectedBucketOwner=account") >= 5  # head, public access block, encryption, policy, put_object
    assert '"aws:SecureTransport": "false"' in deploy and "s3.put_object(Bucket=bucket, Key=key, Body=data, ExpectedBucketOwner=account)" in deploy
    assert "not owned by account" in deploy  # a 403 stops the deploy


@pytest.mark.parametrize("n", ["12", "13", "14"])
def test_2_supply_chain_rows_exist_with_evidence(n):
    r = row(n)
    assert r.count("|") >= 7 and "`" in r.split("|")[-2]  # the last cell (Evidence) names files


def test_2_row_12_says_pip_audit_runs_in_ci_and_ci_does_run_it():
    assert "runs in CI" in row("12")
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "pip-audit" in ci and "infra/serverless/agentcore/requirements.txt" in ci


# ---- 4. Data flows ----------------------------------------------------------------------------------------------------------
def test_4_flow_3_is_the_runtime_to_the_collection_and_3a_is_the_outside_client_to_the_gateway():
    flows = MODEL.split("## Trust boundaries and data flows")[1].split("## Threats")[0]
    f3 = flows.split("3a.")[0]
    assert "SigV4 for `aoss`" in f3 and "Cognito" not in f3.split("4.")[0].split("3. ")[1]
    f3a = flows.split("3a.")[1].split("4. ")[0]
    assert "public AWS endpoint" in f3a and "catalog/search" in f3a and "24 hours" in f3a
    assert "client id and secret from Secrets Manager" in flows.split("4. ")[1]


def test_4_row_1_is_split_by_gateway():
    assert row("1a") and row("1b")
    assert not re.search(r"^\| 1 \|", MODEL, re.M)
    assert "no revocation" in row("1a") and "does not expire" in row("1b")


# ---- the lower-rated items ----------------------------------------------------------------------------------------------------
def test_low_assumptions_evidence_column_and_the_session_id_row():
    assert "## Assumptions" in MODEL and "security group" in MODEL.split("## Assumptions")[1].split("##")[0]
    assert MODEL.count("| Evidence |") == 1  # the threat table has the column
    assert "main.py:156" in row("15") and "runtime context first" in row("15")


# ---- holmes: no inline IAM policies --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path", sorted(str(p.relative_to(ROOT)) for p in list((ROOT / "infra").rglob("*.yaml"))))
def test_no_iam_role_carries_an_inline_policy(path):
    t = load(str(ROOT / path))
    for rid, r in (t.get("Resources") or {}).items():
        if r.get("Type") in ("AWS::IAM::Role", "AWS::IAM::User", "AWS::IAM::Group"):
            assert not r["Properties"].get("Policies"), f"{path}: {rid} has inline policies"


# ---- the evidence column points at lines that exist ---------------------------------------------------------------------------
def _resolve(name: str) -> Path | None:
    hits = [p for p in ROOT.rglob(name) if ".venv" not in p.parts and ".git" not in p.parts]
    return hits[0] if len(hits) == 1 else (sorted(hits, key=lambda p: len(p.parts))[0] if hits else None)


def test_every_file_and_line_cited_in_the_threat_model_exists():
    cites = re.findall(r"`([A-Za-z0-9_./-]+\.(?:yaml|py|sh)):([0-9,\-]+)`", MODEL)
    assert len(cites) > 10
    for name, spec in cites:
        path = (ROOT / name) if (ROOT / name).exists() else _resolve(Path(name).name)
        assert path is not None, f"{name} is cited but not in the repository"
        n_lines = len(path.read_text().splitlines())
        for part in spec.split(","):
            last = int(part.split("-")[-1])
            assert last <= n_lines, f"{name}:{part} is past the end of the file ({n_lines} lines)"


# Each citation that names a specific thing must contain it: a moved line would otherwise leave the model pointing at the wrong code.
ANCHORS = [
    ("infra/serverless/agentcore.yaml", 173, 173, "ReservedConcurrentExecutions"),
    ("infra/serverless/agentcore.yaml", 267, 271, "CustomJWTAuthorizer"),
    ("infra/serverless/agentcore.yaml", 376, 383, "Sid: Logs"),
    ("infra/serverless/agentcore.yaml", 397, 406, "Sid: WorkloadIdentity"),
    ("infra/serverless/agentcore.yaml", 408, 416, "GetSecretValue"),
    ("infra/serverless/agentcore.yaml", 476, 476, "DescribeUserPoolClient"),
    ("infra/cloudformation/inventory-gateway.yaml", 252, 257, "AllowedClients"),
    ("infra/serverless/agentcore/main.py", 68, 94, "def search_client"),
    ("infra/serverless/agentcore/main.py", 97, 109, "get_secret_value"),
    ("infra/serverless/agentcore/main.py", 156, 156, "session_id"),
    ("infra/serverless/deploy_agentcore.py", 71, 71, "AGENT_REQUIREMENTS"),
    ("infra/serverless/deploy_agentcore.py", 121, 123, "only-binary"),
    ("infra/serverless/deploy_agentcore.py", 78, 99, "def ensure_bucket"),
    ("infra/serverless/deploy_agentcore.py", 113, 113, "put_object"),
    ("infra/ec2/install_app.sh", 74, 86, "NoNewPrivileges"),
    ("scripts/deploy_app_to_ec2.py", 122, 147, "generate_presigned_url"),
]


@pytest.mark.parametrize("path,first,last,needle", ANCHORS)
def test_the_cited_lines_contain_what_the_row_says(path, first, last, needle):
    lines = (ROOT / path).read_text().splitlines()
    assert needle in "\n".join(lines[first - 1:last]), f"{path}:{first}-{last} no longer contains {needle!r}: update the Evidence column"
    name = Path(path).name
    assert re.search(rf"`[^`]*{re.escape(name)}:[^`]*{first}", MODEL), f"the threat model no longer cites {name}:{first}"

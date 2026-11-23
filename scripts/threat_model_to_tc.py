#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Write docs/threat-model.tc.json (a Threat Composer v1 export) from the tables in docs/threat-model.md.

The Markdown tables hold the threat, the mitigation, the residual risk and the evidence for each row. The per-row fields
Threat Composer wants in addition (source, prerequisites, impact, goal, assets, priority) are in EXTRA below.

Usage: python scripts/threat_model_to_tc.py [--out docs/threat-model.tc.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRIDE = {"Spoofing": "S", "Tampering": "T", "Repudiation": "R", "Information disclosure": "I", "Denial of service": "D",
          "Elevation of privilege": "E"}

# id: (source, prerequisites, impact, goal, assets, priority)
EXTRA = {
    "1a": ("An outside caller", "who can reach the public catalog Gateway URL", "lets them read the product catalog or run searches at our cost", "confidentiality and availability", ["Product catalog"], "High"),
    "1b": ("An outside caller", "who can reach the public inventory Gateway URL", "lets them read stock data and uses the inventory tool", "confidentiality", ["Inventory bearer token and Cognito client secret"], "High"),
    "2": ("A shopper", "who can pick another shopper in the demo UI", "lets them read another shopper's preferences and facts", "confidentiality", ["Per-shopper memory"], "Medium"),
    "3": ("An author of product text or a user", "who can put text in front of the model", "makes the agent recommend or call tools wrongly", "integrity", ["Product catalog", "Model invocations"], "Medium"),
    "4": ("A shopper", "who can send chat messages", "lets them store a false fact in their own memory", "integrity", ["Per-shopper memory"], "Low"),
    "5": ("An operator or attacker", "with account access", "lets them act without a record of who did what", "accountability", ["AWS account credentials and IAM roles"], "Medium"),
    "6": ("An internet caller", "who knows the collection endpoint", "lets them read the catalog from outside the VPC", "confidentiality", ["Product catalog"], "High"),
    "7": ("A reader of the repository", "with read access to the code or history", "lets them find a secret and use it", "confidentiality", ["Inventory bearer token and Cognito client secret", "AWS account credentials and IAM roles"], "High"),
    "8": ("A shopper", "who calls the app with a different actor id", "lets them read another shopper's memory", "confidentiality", ["Per-shopper memory"], "Medium"),
    "9": ("A caller or a runaway loop", "who can send many requests", "drives model and search cost up", "availability", ["Model invocations"], "Medium"),
    "10": ("A compromised runtime or Lambda", "holding its role", "lets it use permissions beyond its job", "confidentiality and integrity", ["AWS account credentials and IAM roles"], "High"),
    "11": ("An outside caller", "who can reach the EC2 host or its Streamlit port", "lets them use the demo app and its role", "confidentiality", ["AWS account credentials and IAM roles"], "High"),
    "12": ("A compromised dependency", "that is installed at build time", "runs hostile code with the runtime role", "integrity", ["Deployment packages"], "Medium"),
    "13": ("Another account's owner", "who owns the artifact bucket name", "lets them replace the package that gets deployed", "integrity", ["Deployment packages"], "Medium"),
    "14": ("An operator account", "with ssm:SendCommand on the instance", "lets them run commands as root on the host", "integrity", ["Deployment packages", "AWS account credentials and IAM roles"], "Medium"),
    "15": ("A caller who can invoke the runtime", "and knows another shopper's session id", "lets them continue that shopper's conversation", "confidentiality", ["Per-shopper memory"], "Medium"),
}
ASSUMPTIONS = [
    ("The EC2 host has no inbound security group rules and is reached only through SSM Session Manager.", ["11"]),
    ("The account is a non-production sandbox; whoever administers it can already read every secret and package in it.", ["7", "10", "13", "14"]),
    ("Account budget alarms are set by the operator.", ["9"]),
    ("The demo has no real sign-in; the shopper is chosen in the UI.", ["2", "8", "15"]),
]
OPEN_MITIGATION = {"3": "Attach a Bedrock Guardrail to the agent and the model connectors before any wider use (not in the demo)."}


def rows(md: str) -> list[dict]:
    out = []
    for line in md.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if len(cells) >= 6 and re.fullmatch(r"(\d+[ab]?)", cells[0]):
            out.append({"id": cells[0], "stride": cells[1], "threat": cells[2], "mitigation": cells[3], "residual": cells[4], "evidence": cells[5]})
    return out


def build(md: str) -> dict:
    threats, mitigations, links, assumptions, alinks = [], [], [], [], []
    mid = 0
    for n, r in enumerate(rows(md), 1):
        src, pre, impact, goal, assets, prio = EXTRA[r["id"]]
        tid = f"t{n}"
        threats.append({
            "id": tid, "numericId": n, "status": "threatIdentified",
            "statement": f"{src} {pre} can bring about this: {r['threat'][0].lower() + r['threat'][1:]}. That {impact}, resulting in reduced {goal} of {', '.join(assets)}.",
            "threatSource": src, "prerequisites": pre, "threatAction": r["threat"], "threatImpact": impact, "impactedGoal": [goal],
            "impactedAssets": assets, "displayOrder": n,
            "metadata": [{"key": "STRIDE", "value": [STRIDE[r["stride"]]]}, {"key": "Priority", "value": prio},
                         {"key": "Comments", "value": f"{r['id']}. Residual: {r['residual']}. Evidence: {r['evidence']}"}]})
        mid += 1
        mitigations.append({"id": f"m{mid}", "numericId": mid, "status": "mitigationResolved", "content": f"{r['mitigation']} ({r['evidence']})",
                            "metadata": [{"key": "Comments", "value": f"Row {r['id']}"}]})
        links.append({"mitigationId": f"m{mid}", "linkedId": tid})
        if r["id"] in OPEN_MITIGATION:
            mid += 1
            mitigations.append({"id": f"m{mid}", "numericId": mid, "status": "mitigationIdentified", "content": OPEN_MITIGATION[r["id"]]})
            links.append({"mitigationId": f"m{mid}", "linkedId": tid})
    index = {r["id"]: f"t{n}" for n, r in enumerate(rows(md), 1)}
    for n, (text, ids) in enumerate(ASSUMPTIONS, 1):
        assumptions.append({"id": f"a{n}", "numericId": n, "content": text})
        alinks += [{"assumptionId": f"a{n}", "linkedId": index[i], "type": "Threat"} for i in ids if i in index]
    return {
        "schema": 1, "applicationInfo": {"name": "Sample retail agent demo",
                                         "description": "Sample content: an agent that searches a public product catalog on OpenSearch, with per-shopper memory. Non-production, no customer data."},
        "architecture": {"description": "See docs/architecture.md: Streamlit app on EC2 (SSM only), AgentCore runtime in a VPC, OpenSearch Serverless collection or domain, Bedrock, two Gateways, Cognito."},
        "dataflow": {"description": "See 'Trust boundaries and data flows' in docs/threat-model.md (flows 1 to 5, 3a)."},
        "assumptions": assumptions, "assumptionLinks": alinks, "threats": threats, "mitigations": mitigations, "mitigationLinks": links}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "docs/threat-model.tc.json")
    args = ap.parse_args()
    model = build((ROOT / "docs/threat-model.md").read_text())
    args.out.write_text(json.dumps(model, indent=2) + "\n")
    print(f"wrote {args.out} ({len(model['threats'])} threats, {len(model['mitigations'])} mitigations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Environment-driven configuration. No secrets are ever stored in the repo."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from retail_agent_demo import proc

REPO_ROOT = Path(__file__).resolve().parents[2]


def ids_path() -> Path:
    """The file the deploy scripts write the created object IDs to (gitignored)."""
    return REPO_ROOT / "config" / "ids.json"


def env_path() -> Path:
    return REPO_ROOT / ".env"


@dataclass
class Settings:
    host: str
    local_url: str
    region: str
    auth: str  # "sigv4" (default) | "basic"
    user: str | None
    password: str | None
    verify_tls: bool
    rerank_region: str
    rerank_model_arn: str
    connector_role_arn: str | None
    name_prefix: str = "sample-agent-ret"  # prefix of every OpenSearch object name; must equal the collection stack's NamePrefix
    extra_rerank_regions: list = field(default_factory=list)
    ids: dict = field(default_factory=dict)
    service: str = "aoss"  # SigV4 service name of an Amazon OpenSearch Serverless collection

    def id(self, key: str) -> str:
        """Look up a deployed-object ID; fail with a pointer to the fix."""
        value = self.ids.get(key)
        if not value or value.startswith("<"):
            raise KeyError(
                f"'{key}' is not set in {ids_path()}. "
                "Run the matching infra/ script or copy config/ids.example.json and fill it in."
            )
        return value


def _resolve_password(var: str = "OS_PASS") -> str | None:
    """Read a password from $<var>, or from the command in $<var>_CMD (never both stored in the repo)."""
    if os.environ.get(var):
        return os.environ[var]
    cmd = os.environ.get(f"{var}_CMD")
    if cmd:
        # OS_PASS_CMD is the operator's own command from their local .env (for example a keychain lookup). It is
        # started directly, without a shell, so pipes and redirects are not interpreted; it never contains remote
        # or model-controlled input.
        return proc.read_command_output(cmd)
    return None


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    load_dotenv(env_file or env_path())
    rerank_region = os.environ.get("RERANK_REGION", "us-east-1")
    ids = {}
    if ids_path().exists():
        ids = json.loads(ids_path().read_text())
    return Settings(
        host=os.environ["OS_HOST"],
        local_url=os.environ.get("OS_LOCAL_URL", "https://localhost:9200").rstrip("/"),
        region=os.environ.get("OS_REGION", "us-east-1"),
        auth=os.environ.get("OS_AUTH", "sigv4"),
        user=os.environ.get("OS_USER"),
        password=_resolve_password(),
        verify_tls=os.environ.get("OS_VERIFY_TLS", "true").lower() == "true",
        rerank_region=rerank_region,
        rerank_model_arn=os.environ.get("RERANK_MODEL_ARN", f"arn:aws:bedrock:{rerank_region}::foundation-model/cohere.rerank-v3-5:0"),
        connector_role_arn=os.environ.get("BEDROCK_CONNECTOR_ROLE_ARN"),
        name_prefix=os.environ.get("NAME_PREFIX", "sample-agent-ret"),
        extra_rerank_regions=[r.strip() for r in os.environ.get("RERANK_EXTRA_REGIONS", "").split(",") if r.strip()],
        ids=ids,
        service=os.environ.get("OS_SERVICE", "aoss"),
    )


def save_ids(updates: dict) -> dict:
    """Merge updates into config/ids.json (gitignored) and return the result."""
    path = ids_path()
    ids = json.loads(path.read_text()) if path.exists() else {}
    ids.update(updates)
    path.write_text(json.dumps(ids, indent=2) + "\n")
    return ids

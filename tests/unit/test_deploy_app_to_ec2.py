# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The EC2 deployment helper: what goes to the instance, and what must never (no network)."""
import importlib.util
import io
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("deploy_app_to_ec2", ROOT / "scripts/deploy_app_to_ec2.py")
deploy = importlib.util.module_from_spec(spec)
sys.modules["deploy_app_to_ec2"] = deploy
spec.loader.exec_module(deploy)


def names(blob: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        return tar.getnames()


def test_the_tarball_carries_the_source_and_ids_but_no_secrets_or_data():
    listed = names(deploy.build_tarball())
    assert "pyproject.toml" in listed and any(n.startswith("src/retail_agent_demo/app.py") for n in listed)
    for forbidden in (".env", "data", ".venv", "config/.env.example"):
        assert not any(n == forbidden or n.startswith(forbidden + "/") for n in listed), forbidden
    assert not any("__pycache__" in n or n.endswith(".pyc") or ".egg-info" in n for n in listed)
    assert not any(n.startswith("config/") and n != "config/ids.json" for n in listed)


def test_the_installer_listens_on_loopback_only_and_runs_as_a_service_user():
    text = (ROOT / "infra/ec2/install_app.sh").read_text()
    assert "--server.address 127.0.0.1" in text and "0.0.0.0" not in text  # nosec B104 - test only
    assert "User=sample-agent-ret" in text and "NoNewPrivileges=true" in text
    assert '${RUN_DIR}/env' in text and 'RUN_DIR="${APP_RUN_DIR:-/run/sample-agent-ret}"' in text  # secrets land in tmpfs, not on disk
    assert 'BASE="${APP_BASE:-/opt/sample-agent-ret}"' in text and 'SVC="${APP_SERVICE:-sample-agent-ret-app}"' in text
    assert 'PORT="${APP_PORT:-8503}"' in text
    # the saved outputs live outside the app directory, so a deploy (which replaces it) does not wipe them
    assert 'Environment=APP_STATE_DIR=${BASE}/state' in text and '"$BASE/state"' in text


IDS = {"collection_host": "abc.aoss.us-east-1.on.aws", "endpoint_dns": "vpce-1.aoss-data.us-east-1.vpce.amazonaws.com",
       "app_role_arn": "arn:aws:iam::123456789012:role/sample-agent-ret-app"}


def test_the_serverless_app_environment_has_no_secret_and_runs_as_the_app_role():
    env = deploy.build_env(IDS, {"NAME_PREFIX": "sample-agent-ret"}, "/opt/sample-agent-ret")
    assert 'OS_LOCAL_URL="https://vpce-1.aoss-data.us-east-1.vpce.amazonaws.com"' in env  # the private endpoint, from inside the VPC
    assert 'OS_HOST="abc.aoss.us-east-1.on.aws"' in env and 'OS_SERVICE="aoss"' in env
    assert 'AWS_PROFILE="app"' in env and 'AWS_CONFIG_FILE="/opt/sample-agent-ret/aws-config"' in env
    assert not any(k in env for k in ("PASS", "SECRET", "TOKEN", "KEY"))


def test_the_serverless_app_refuses_to_deploy_without_its_role_and_endpoint():
    with pytest.raises(SystemExit, match="app_role_arn"):
        deploy.build_env({k: v for k, v in IDS.items() if k != "app_role_arn"}, {}, "/opt/x")


def test_the_install_ships_the_ids_file_and_runs_as_its_own_service():
    assert "config/ids.json" in deploy.INSTALL["include"] and deploy.INSTALL["service"] == "sample-agent-ret-app"


def test_the_tarball_ships_the_ids_and_never_an_env_file(tmp_path, monkeypatch):
    """Built from a scratch tree, so it does not depend on the (gitignored) ids files of a real deployment."""
    import io
    import tarfile
    for rel in ("pyproject.toml", "config/ids.json", ".env", "src/retail_agent_demo/app.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    names = tarfile.open(fileobj=io.BytesIO(deploy.build_tarball(deploy.INSTALL["include"]))).getnames()
    assert "config/ids.json" in names
    assert not any(n.split("/")[-1].startswith(".env") for n in names)


def test_the_tarball_carries_the_product_pictures_so_the_hosted_cards_show_them():
    listed = names(deploy.build_tarball())
    assert "assets/products/manifest.json" in listed
    assert sum(n.startswith("assets/products/") and n.endswith(".jpg") for n in listed) >= 300

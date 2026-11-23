#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Install (or update) the demo app on an EC2 host in the collection's VPC, so it reaches the collection directly.

Why: on your laptop the app depends on a port forward to the private collection endpoint and on short-lived laptop
credentials. On an instance inside the VPC the app talks to the collection itself, as the app IAM role; you only forward
one port to the app.

What it does, with the credentials of your AWS CLI environment (run it under your login tooling):
  1. builds a tarball of src/, assets/ (the product pictures), pyproject.toml and config/ids.json (never .env or any other config)
  2. stores the app's environment file as an SSM SecureString parameter (the file holds no secrets: the app runs as an IAM role)
  3. stages the tarball in a private, short-lived S3 bucket and hands the instance a 15-minute pre-signed URL
  4. runs infra/ec2/install_app.sh on the instance through SSM Run Command and prints its result
  5. deletes the bucket again

Usage: python scripts/deploy_app_to_ec2.py --instance i-0123456789abcdef0 [--dry-run]
"""

from __future__ import annotations

import argparse
import io
import sys
import tarfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from retail_agent_demo.config import load_settings  # noqa: E402
from retail_agent_demo.waits import pause  # noqa: E402

INSTALLER = ROOT / "infra/ec2/install_app.sh"
INSTALL = {"include": ["pyproject.toml", "config/ids.json", "src", "assets"],  # assets: the generated product pictures the cards show
           "parameter": "/sample-agent-ret/app-env", "base": "/opt/sample-agent-ret", "service": "sample-agent-ret-app", "port": "8503",
           "run_dir": "/run/sample-agent-ret"}
SKIP_PARTS = {"__pycache__", ".pytest_cache"}


def build_tarball(include: list[str] | None = None) -> bytes:
    """The app's source and its object IDs. Nothing else: no .env, no other config, no data."""
    buf = io.BytesIO()

    def keep(info: tarfile.TarInfo):
        parts = Path(info.name).parts
        if SKIP_PARTS & set(parts) or any(p.endswith(".egg-info") for p in parts) or info.name.endswith(".pyc"):
            return None
        return info

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in include or INSTALL["include"]:
            path = ROOT / rel
            if path.exists():
                tar.add(path, arcname=rel, filter=keep)
    return buf.getvalue()


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_env(ids: dict, settings_env: dict[str, str], base: str) -> str:
    """The Serverless app's environment file: where the collection is and which role to run as. No secrets at all."""
    for key in ("collection_host", "endpoint_dns", "app_role_arn"):
        if not ids.get(key):
            raise SystemExit(f"config/ids.json has no '{key}': run infra/serverless/deploy_agentcore.py --app-instance <id>")
    values = {"OS_HOST": ids["collection_host"], "OS_LOCAL_URL": f"https://{ids['endpoint_dns']}",
              "OS_REGION": settings_env.get("OS_REGION", "us-east-1"), "OS_AUTH": "sigv4", "OS_SERVICE": "aoss",
              "NAME_PREFIX": settings_env.get("NAME_PREFIX", "sample-agent-ret"), "INVENTORY_MCP_ENABLED": "1",
              "AWS_CONFIG_FILE": f"{base}/aws-config", "AWS_PROFILE": "app"}
    return "\n".join(f"{k}={_quote(v)}" for k, v in values.items()) + "\n"


def wait_for(ssm, command_id: str, instance: str, timeout_s: int = 900) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pause(5)
        try:
            inv = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if inv["Status"] not in ("Pending", "InProgress", "Delayed"):
            return inv
    raise SystemExit("timed out waiting for the install command")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", required=True, help="EC2 instance id (SSM-managed, in the domain's VPC)")
    ap.add_argument("--region", default=None, help="Region of the host (default: OS_REGION from .env)")
    ap.add_argument("--parameter", default=INSTALL["parameter"], help="SSM SecureString parameter for the env file")
    ap.add_argument("--dry-run", action="store_true", help="build and check everything, change nothing")
    args = ap.parse_args()

    import boto3
    from botocore.config import Config
    from dotenv import dotenv_values

    s = load_settings()
    args.region = args.region or s.region
    env_file = build_env(s.ids, dotenv_values(ROOT / ".env"), INSTALL["base"])
    tarball = build_tarball()
    installer = INSTALLER.read_text()
    print(f"tarball {len(tarball) // 1024} KB; env file for {env_file.count(chr(10))} variables "
          f"(runs as {s.ids['app_role_arn'].split('/')[-1]}); installer {len(installer.splitlines())} lines")
    if args.dry_run:
        print("dry run: nothing changed")
        return

    ssm = boto3.client("ssm", region_name=args.region)
    # a regional endpoint and SigV4, so the pre-signed URL works at once (a new bucket's global URL only redirects)
    s3 = boto3.client("s3", region_name=args.region, endpoint_url=f"https://s3.{args.region}.amazonaws.com",
                      config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
    account = boto3.client("sts").get_caller_identity()["Account"]
    bucket = f"sample-agent-ret-demo-deploy-{account}-{uuid.uuid4().hex[:8]}"

    ssm.put_parameter(Name=args.parameter, Value=env_file, Type="SecureString", Overwrite=True,
                      Description="Sample demo app environment (least-privilege user)")
    print(f"stored {args.parameter}")
    s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": args.region})
    try:
        s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
        s3.put_object(Bucket=bucket, Key="app.tar.gz", Body=tarball)
        url = s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": "app.tar.gz"}, ExpiresIn=900)
        extra = (f" APP_BASE='{INSTALL['base']}' APP_SERVICE='{INSTALL['service']}' APP_PORT='{INSTALL['port']}'"
                 f" APP_RUN_DIR='{INSTALL['run_dir']}' APP_ROLE_ARN='{s.ids['app_role_arn']}'")
        commands = [f"export APP_TARBALL_URL='{url}' ENV_PARAMETER='{args.parameter}' AWS_REGION='{args.region}'{extra}",
                    "cat > /tmp/install_app.sh <<'SAMPLE_EOS'", *installer.splitlines(), "SAMPLE_EOS",
                    "bash /tmp/install_app.sh; rc=$?; rm -f /tmp/install_app.sh; exit $rc"]
        sent = ssm.send_command(InstanceIds=[args.instance], DocumentName="AWS-RunShellScript",
                                Comment="sample-agent-ret demo app install", Parameters={"commands": commands},
                                TimeoutSeconds=900)
        inv = wait_for(ssm, sent["Command"]["CommandId"], args.instance)
        print(f"install: {inv['Status']}")
        print(inv.get("StandardOutputContent", "")[-1500:])
        if inv["Status"] != "Success":
            print(inv.get("StandardErrorContent", "")[-1500:])
            raise SystemExit(1)
    finally:
        try:
            s3.delete_object(Bucket=bucket, Key="app.tar.gz")
            s3.delete_bucket(Bucket=bucket)
            print(f"removed staging bucket {bucket}")
        except Exception as e:  # noqa: BLE001 - cleanup must not hide the install result
            print(f"could not remove staging bucket {bucket}: {e}")


if __name__ == "__main__":
    main()

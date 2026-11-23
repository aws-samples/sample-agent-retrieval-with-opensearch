#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deploy the AgentCore side of the Serverless track (additive; nothing existing is modified).

  1. a private S3 bucket for code packages (created if missing)
  2. the search Lambda package, uploaded
  3. a secret holding the inventory Gateway's Cognito client credentials, READ from the existing inventory stack and
     stored in a new secret, so the runtime can call that Gateway without touching it (skipped with --no-inventory)
  4. the Amazon OpenSearch Memory space that holds the shoppers' memories (created if missing; CloudFormation has no
     resource for it). The service is in a few Regions only, so the space is in us-east-1 whatever the app's Region
  5. CloudFormation stack sample-agent-ret-agentcore (search Gateway + Lambda, roles incl. the app's access to the space, data access policy)
  6. the agent runtime package (Strands agent, Linux arm64 dependencies), uploaded, and the runtime created or updated
  7. ids written to config/ids.json

Needs the collection stack (sample-agent-ret-collection) and infra/serverless/deploy_objects.py to have run.
Usage (under your login tooling): python infra/serverless/deploy_agentcore.py [--no-inventory] [--skip-runtime]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from retail_agent_demo import proc, search  # noqa: E402
from retail_agent_demo.config import load_settings, save_ids  # noqa: E402
from retail_agent_demo.waits import pause  # noqa: E402

HERE = Path(__file__).resolve().parent
REGION = "us-east-1"  # replaced in main() by the Region of config/.env (OS_REGION)
STACK, COLLECTION_STACK, INVENTORY_STACK = "sample-agent-ret-agentcore", "sample-agent-ret-collection", "sample-agent-ret-inventory-gateway"
RUNTIME_NAME = "sample_agent_ret_shopping_agent"
SECRET_NAME = "sample-agent-ret/inventory-client"  # nosec B105 - the name of a Secrets Manager secret, not a password  # pragma: allowlist secret
MEMORY_REGIONS = ("us-east-1", "us-west-2", "ap-northeast-1", "eu-central-1")  # where OpenSearch Memory is offered
MEMORY_REGION = "us-east-1"  # replaced in main(): MEMORY_REGION from the environment, else the app's Region if it has the service
SPACE_NAME = "sample-agent-ret-shopper-memory"
SPACE_EXTRACTION_MODEL = "anthropic.claude-haiku-5-5"
# What the shopper's memory keeps. The built-in SEMANTIC strategy sometimes left the budget out and, when the budget changed, merged the new
# amount into the memory of a product ("budget now $40, replacing the earlier $24.99 plan"); a CUSTOM strategy that wrote the budget
# itself still kept the old amount beside the new or hedged it. So the app writes the budget directly (osmemory.set_budget, updated in
# place), and this prompt covers everything else.
SHOPPER_PROMPT = (
    'You keep long-term memory about one shopper, called "the user". Write short notes about what the new turns tell you about the user.\n'
    "- Take notes from what the user says. Assistant turns, product titles and product prices are context only.\n"
    "- Note: the user's name; who they shop for (name, age, interests); occasions and dates; sizes, colours and brands; and who is helping "
    "to pay.\n"
    "- Do not write a note about the budget: the application records it.\n"
    "- A product's price is never a fact about the user. Note a product the user chose or plans to buy by its name, without its price.\n"
    "- Every note is one plain sentence that makes sense without the conversation. Use the turn's date to turn relative dates into "
    "calendar dates.\n"
    "- Write each fact once. Do not guess, and write nothing when nothing is worth keeping.")
# One CUSTOM strategy with that prompt. It extracts after 12 messages or 10 s of quiet (so a turn is extracted about 10 s after it), seeds
# each extraction with the 5 most relevant stored memories to avoid duplicates, and consolidates, so a new budget replaces the old one.
SPACE_STRATEGIES = [{"type": "CUSTOM", "name": "shopper-facts", "description": "Facts and preferences about one shopper",
                     "configuration": {"extractionPrompt": SHOPPER_PROMPT, "extractEveryNMessages": 12, "idleTimeoutSeconds": 10,
                                       "extractionSeedTopK": 5, "consolidationEnabled": True}}]
AGENT_REQUIREMENTS = HERE / "agentcore" / "requirements.txt"  # every runtime dependency, pinned (see the file's header)


def outputs(cfn, stack: str) -> dict:
    return {o["OutputKey"]: o["OutputValue"] for o in cfn.describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]}


def ensure_bucket(s3, name: str, account: str) -> None:
    """Use the code bucket only if this account owns it, create it if it does not exist, and lock it down on every run.

    The name is predictable (it ends in the account id), so the owner is checked: a bucket of that name in another
    account is refused, not written to. The bucket refuses plain-HTTP requests, public access and unencrypted objects."""
    try:
        s3.head_bucket(Bucket=name, ExpectedBucketOwner=account)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code not in ("404", "NoSuchBucket"):
            raise SystemExit(f"bucket {name} exists but is not owned by account {account} ({code}); not using it") from e
        s3.create_bucket(Bucket=name, **({} if REGION == "us-east-1" else {"CreateBucketConfiguration": {"LocationConstraint": REGION}}))  # us-east-1 takes no constraint
        print(f"  bucket created {name}")
    s3.put_public_access_block(Bucket=name, ExpectedBucketOwner=account, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    s3.put_bucket_encryption(Bucket=name, ExpectedBucketOwner=account, ServerSideEncryptionConfiguration={"Rules": [
        {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]})
    s3.put_bucket_policy(Bucket=name, ExpectedBucketOwner=account, Policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Sid": "DenyInsecureTransport", "Effect": "Deny", "Principal": "*", "Action": "s3:*",
                       "Resource": [f"arn:aws:s3:::{name}", f"arn:aws:s3:::{name}/*"],
                       "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}))


def zip_dir(root: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".pyc"):
                z.write(p, p.relative_to(root).as_posix())
    return buf.getvalue()


def upload(s3, bucket: str, prefix: str, data: bytes, account: str) -> str:
    key = f"{prefix}-{hashlib.sha256(data).hexdigest()[:12]}.zip"
    s3.put_object(Bucket=bucket, Key=key, Body=data, ExpectedBucketOwner=account)
    print(f"  uploaded s3://{bucket}/{key} ({len(data) // 1024} KB)")
    return key


def build_runtime_package() -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        build = Path(tmp)
        proc.run([sys.executable, "-m", "pip", "install", "-q", "--target", str(build), "--platform",
                        "manylinux2014_aarch64", "--python-version", "3.12", "--implementation", "cp",
                        "--only-binary=:all:", "-r", str(AGENT_REQUIREMENTS)], check=True)
        shutil.copytree(ROOT / "src" / "retail_agent_demo", build / "retail_agent_demo",
                        ignore=shutil.ignore_patterns("__pycache__", "app.py"))
        shutil.copy(HERE / "agentcore" / "main.py", build / "main.py")
        return zip_dir(build)


def ensure_inventory_secret(cfn, sm, cognito, dry: bool) -> str:
    """Copy the inventory Gateway's client credentials into a NEW secret (reads only from the existing stack)."""
    try:
        return sm.describe_secret(SecretId=SECRET_NAME)["ARN"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
    out = outputs(cfn, INVENTORY_STACK)
    client = cognito.describe_user_pool_client(UserPoolId=out["UserPoolId"], ClientId=out["AppClientId"])["UserPoolClient"]
    value = {"client_id": client["ClientId"], "client_secret": client["ClientSecret"],
             "token_endpoint": out["TokenEndpoint"], "scope": "inventory/invoke"}
    if dry:
        print(f"  would create secret {SECRET_NAME}")
        return ""
    arn = sm.create_secret(Name=SECRET_NAME, SecretString=json.dumps(value),
                           Description="Inventory Gateway client credentials for the Serverless track's agent runtime",
                           Tags=[{"Key": "project", "Value": "sample-agent-ret"}])["ARN"]
    print(f"  secret created {SECRET_NAME}")
    return arn


def ensure_memory_space(osm) -> str:
    """The id of the OpenSearch Memory space named SPACE_NAME, created (and waited for) if it does not exist.

    An existing space is left as it is: its strategies and retention can be changed with UpdateSpace, and the encryption key
    cannot be changed after creation. Names are unique in the account and Region."""
    token = None
    while True:
        page = osm.list_spaces(**({"nextToken": token} if token else {}))
        for sp in page.get("spaces", []):
            if sp["name"] == SPACE_NAME and sp["status"] not in ("DELETING", "DELETED"):
                osm.get_waiter("space_active").wait(spaceId=sp["spaceId"])
                print(f"  space {sp['spaceId']} exists")
                return sp["spaceId"]
        token = page.get("nextToken")
        if not token:
            break
    made = osm.create_space(name=SPACE_NAME, memoryStrategies=SPACE_STRATEGIES, extractionModelId=SPACE_EXTRACTION_MODEL,
                            eventExpiryDays=30, tags=[{"key": "project", "value": "sample-agent-ret"}, {"key": "track", "value": "serverless"}])
    print(f"  space {made['spaceId']} created; waiting for it to become active")
    osm.get_waiter("space_active").wait(spaceId=made["spaceId"])
    return made["spaceId"]


def deploy_stack(cfn, params: dict) -> None:
    template = (HERE / "agentcore.yaml").read_text()
    args = dict(StackName=STACK, TemplateBody=template, Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
                Parameters=[{"ParameterKey": k, "ParameterValue": v} for k, v in params.items()],
                Tags=[{"Key": "project", "Value": "sample-agent-ret"}, {"Key": "track", "Value": "serverless"}])
    try:
        cfn.describe_stacks(StackName=STACK)
        action, waiter = "update", "stack_update_complete"
        try:
            cfn.update_stack(**args)
        except ClientError as e:
            if "No updates are to be performed" in str(e):
                print("  stack is up to date")
                return
            raise
    except ClientError as e:
        if "does not exist" not in str(e):
            raise
        action, waiter = "create", "stack_create_complete"
        cfn.create_stack(**args)
    print(f"  stack {action} started; waiting")
    cfn.get_waiter(waiter).wait(StackName=STACK, WaiterConfig={"Delay": 10, "MaxAttempts": 90})


def ensure_runtime(ctl, package: dict, role_arn: str, sg: str, subnets: list[str], env: dict) -> tuple[str, str]:
    artifact = {"codeConfiguration": {"code": {"s3": package}, "runtime": "PYTHON_3_12", "entryPoint": ["main.py"]}}
    network = {"networkMode": "VPC", "networkModeConfig": {"securityGroups": [sg], "subnets": subnets}}
    existing = next((r for page in ctl.get_paginator("list_agent_runtimes").paginate() for r in page["agentRuntimes"]
                     if r["agentRuntimeName"] == RUNTIME_NAME), None)
    if existing:
        rid = existing["agentRuntimeId"]
        ctl.update_agent_runtime(agentRuntimeId=rid, agentRuntimeArtifact=artifact, roleArn=role_arn,
                                 networkConfiguration=network, environmentVariables=env,
                                 description="Shopping assistant for the Sample Serverless track")
        print(f"  runtime update started {rid}")
    else:
        r = ctl.create_agent_runtime(agentRuntimeName=RUNTIME_NAME, agentRuntimeArtifact=artifact, roleArn=role_arn,
                                     networkConfiguration=network, environmentVariables=env,
                                     description="Shopping assistant for the Sample Serverless track",
                                     tags={"project": "sample-agent-ret"})
        rid = r["agentRuntimeId"]
        print(f"  runtime create started {rid}")
    for _ in range(90):
        rt = ctl.get_agent_runtime(agentRuntimeId=rid)
        if rt["status"] in ("READY", "CREATE_FAILED", "UPDATE_FAILED"):
            break
        pause(10)
    if rt["status"] != "READY":
        raise SystemExit(f"runtime {rid} is {rt['status']}: {rt.get('failureReason', '')}")
    print(f"  runtime READY {rid}")
    return rid, rt["agentRuntimeArn"]


def inference_profile_prefix(region: str) -> str:
    """The cross-Region inference profile prefix of the Region's geography (us., eu., apac.). Check that the model is offered in
    your Region before you deploy: pass --agent-model-id to use another model or profile."""
    return {"us": "us", "eu": "eu", "ap": "apac"}.get(region.split("-")[0], "us")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-inventory", action="store_true", help="do not wire the inventory Gateway tools")
    ap.add_argument("--agent-model-id", help="Bedrock model or inference profile id the hosted agent uses (default: the runtime's own, Claude Sonnet 4.5)")
    ap.add_argument("--skip-runtime", action="store_true", help="stack only (Gateway, Lambda, roles) and the memory space")
    ap.add_argument("--app-instance", help="EC2 instance that hosts the app: its role may assume the new sample-agent-ret-app role "
                                            "(the app uses nothing else of that role)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = load_settings()
    global REGION, MEMORY_REGION
    REGION = s.region
    MEMORY_REGION = os.environ.get("MEMORY_REGION") or (REGION if REGION in MEMORY_REGIONS else "us-east-1")
    prefix = inference_profile_prefix(REGION)
    cfn, s3, sm = (boto3.client(n, region_name=REGION) for n in ("cloudformation", "s3", "secretsmanager"))
    cognito, ctl, ec2 = (boto3.client(n, region_name=REGION) for n in ("cognito-idp", "bedrock-agentcore-control", "ec2"))
    account = boto3.client("sts").get_caller_identity()["Account"]
    coll = outputs(cfn, COLLECTION_STACK)
    host = coll["CollectionEndpoint"].removeprefix("https://")
    endpoint = ec2.describe_vpc_endpoints(VpcEndpointIds=[coll["DataEndpointId"]])["VpcEndpoints"][0]["DnsEntries"][0]["DnsName"]
    collection_params = {p["ParameterKey"]: p["ParameterValue"]
                         for p in cfn.describe_stacks(StackName=COLLECTION_STACK)["Stacks"][0]["Parameters"]}
    vpc, subnets = collection_params["VpcId"], collection_params["SubnetIds"]
    bucket = f"sample-agent-ret-code-{account}"

    print("code bucket and search Lambda package")
    if args.dry_run:
        print(f"  would use bucket {bucket}, collection host {host}")
        return
    ensure_bucket(s3, bucket, account)
    lambda_key = upload(s3, bucket, "search-tool", zip_dir_of_file(HERE / "agentcore" / "search_lambda.py"), account)

    secret_arn = "" if args.no_inventory else ensure_inventory_secret(cfn, sm, cognito, args.dry_run)
    trusted = ""
    if args.app_instance:
        profile = ec2.describe_instances(InstanceIds=[args.app_instance])["Reservations"][0]["Instances"][0]["IamInstanceProfile"]["Arn"]
        iam = boto3.client("iam")
        trusted = iam.get_instance_profile(InstanceProfileName=profile.split("/")[-1])["InstanceProfile"]["Roles"][0]["Arn"]
        print(f"  the app role will be assumable by {trusted.split('/')[-1]} (the role of {args.app_instance})")
    if not trusted:  # a re-run without --app-instance must not remove the app role: keep what the stack has
        try:
            old = {p["ParameterKey"]: p["ParameterValue"] for p in cfn.describe_stacks(StackName=STACK)["Stacks"][0]["Parameters"]}
            trusted = old.get("AppTrustedPrincipalArn", "")
        except ClientError:
            pass
    rv = search.retrieval_from(s.ids)  # the index, pipeline and picture model that config/ids.json names
    print("OpenSearch Memory space")
    space_id = ensure_memory_space(boto3.client("opensearchmemory", region_name=MEMORY_REGION))
    print("CloudFormation stack")
    deploy_stack(cfn, {
        "MemorySpaceId": space_id, "MemoryRegion": MEMORY_REGION,
        "CodeBucket": bucket, "SearchLambdaKey": lambda_key, "VpcId": vpc, "SubnetIds": subnets, "ClientCidr": collection_params["ClientCidr"],
        "CollectionHost": host, "CollectionId": coll["CollectionId"], "EndpointDns": endpoint, "EmbeddingModelId": s.id("embedding_model_id"),
        "Index": rv.index, "SearchPipeline": rv.pipeline or "sample-agent-ret-hybrid",
        "AgentModelId": f"{prefix}.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "VectorFields": ",".join(rv.vector_fields), "ImageModelId": rv.image_model_id or "",
        "InventoryClientSecretArn": secret_arn,
        "AppTrustedPrincipalArn": trusted})
    out = outputs(cfn, STACK)
    ids = {"space_id": space_id, "memory_region": MEMORY_REGION, "catalog_gateway_url": out["GatewayUrl"],
           "catalog_token_endpoint": out["TokenEndpoint"], "catalog_user_pool_id": out["UserPoolId"],
           "catalog_client_id": out["AppClientId"], **({"app_role_arn": out["AppRoleArn"]} if "AppRoleArn" in out else {}), "collection_host": host, "endpoint_dns": endpoint}

    if not args.skip_runtime:
        print("agent runtime package (installing Linux arm64 dependencies)")
        key = upload(s3, bucket, "agent-runtime", build_runtime_package(), account)
        env = {"COLLECTION_HOST": host, "ENDPOINT_DNS": endpoint,
               "INDEX": rv.index, "SEARCH_PIPELINE": rv.pipeline or "sample-agent-ret-hybrid",
               "EMBEDDING_MODEL_ID": s.id("embedding_model_id"), "VECTOR_FIELDS": ",".join(rv.vector_fields)}
        if rv.image_model_id:
            env["IMAGE_MODEL_ID"] = rv.image_model_id
        env["OS_REGION"] = REGION
        env["AGENT_MODEL_ID"] = args.agent_model_id or f"{prefix}.anthropic.claude-haiku-4-5-20251001-v1:0"
        if secret_arn:
            inv = outputs(cfn, INVENTORY_STACK)
            env.update({"INVENTORY_CLIENT_SECRET_ARN": secret_arn, "INVENTORY_GATEWAY_URL": inv["GatewayUrl"]})
        rid, arn = ensure_runtime(ctl, {"bucket": bucket, "prefix": key}, out["RuntimeRoleArn"],
                                  out["RuntimeSecurityGroupId"], subnets.split(","), env)
        ids.update({"agent_runtime_id": rid, "agent_runtime_arn": arn})
    save_ids(ids)
    print("ids written to config/ids.json")


def zip_dir_of_file(path: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(path, path.name)
    return buf.getvalue()


if __name__ == "__main__":
    main()

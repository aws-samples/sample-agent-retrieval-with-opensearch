# Setup guide

This guide deploys the whole sample into one AWS account and Region, step by step. Plan on about two to three hours, most
of it waiting: the collection takes about ten minutes to create, and embedding the catalog takes an hour or more.

> **Use a non-production account.** The stacks create IAM roles, an OpenSearch Serverless collection, an AgentCore runtime
> and a memory space. Several of them bill while they exist: read [cost-model.md](cost-model.md) first, and
> [clean up](#clean-up) when you are done.

## 1. What you need

| Need | Detail |
|---|---|
| AWS account | A non-production account. You deploy with a role that can create IAM roles, CloudFormation stacks, OpenSearch Serverless collections, AgentCore resources, OpenSearch Memory spaces, S3 buckets, Lambda functions, Cognito user pools and Secrets Manager secrets. An administrator role is the simplest for a sandbox. |
| Tools | [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), the [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html), Python 3.11 or newer. [Graphviz](https://graphviz.org/download/) only if you redraw the diagrams. |
| Network | A VPC with three **private** subnets in three Availability Zones, and a NAT gateway. The collection's interface endpoint goes in those subnets, and the agent runtime runs in them and reaches Bedrock through the NAT gateway. If you have no such VPC, deploy the optional `infra/serverless/network.yaml` (a VPC with three private subnets, a NAT gateway and a flow log; a NAT gateway bills by the hour), or use the VPC console's "VPC and more" wizard. |
| Model access | In Amazon Bedrock, request access to Titan Text Embeddings v2 (in your Region), Titan Multimodal Embeddings (in `us-east-1` or `us-west-2`), Cohere Rerank 3.5 and Claude Haiku 4.5. Memory fact extraction uses a model that the OpenSearch Memory service calls itself, so there is no access to request for it. |
| Regions | Pick a Region that offers OpenSearch Serverless (vector search collections), Bedrock AgentCore (Runtime and Gateway), Titan Text Embeddings v2 and Claude Haiku 4.5, and that has at least three Availability Zones (`network.yaml` uses three). Set it as `OS_REGION` and `AWS_REGION` (`us-east-1` in the examples). OpenSearch Memory exists in `us-east-1`, `us-west-2`, `ap-northeast-1` and `eu-central-1` only: the space is created in your Region if it is one of those, and otherwise in `us-east-1` (set `MEMORY_REGION` to choose). The agent's Claude model uses the cross-Region inference profile of your geography (`us.`, `eu.` or `apac.`); pass `--agent-model-id` to `deploy_agentcore.py` if your Region needs another. Cohere Rerank is called in `RERANK_REGION` (`us-east-1` by default). Titan Multimodal Embeddings is called in `MULTIMODAL_REGION` (default `us-east-1`). |
| Quotas | Check these before the catalog load: the OpenSearch Serverless collection and OCU limits for your account, the Titan Text Embeddings v2 request rate (the default of about 6,000 requests per minute sets how fast the catalog loads), and the per-Region rate of Cohere Rerank (250 requests per minute for version 3.5, not adjustable). |
| A way into the VPC | The collection is private. You reach it through an SSM port forward to its endpoint, so you need an SSM-managed EC2 instance in the VPC. [Step 4](#4-a-host-inside-the-vpc) shows how to create one. |

Check your identity and Region before you start, and keep the same shell for every command below:

```bash
aws sts get-caller-identity
export AWS_REGION=us-east-1
```

## 2. Get the code and install

```bash
git clone https://github.com/aws-samples/sample-agent-retrieval-with-opensearch.git
cd sample-agent-retrieval-with-opensearch
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/unit          # no network needed; all of it should pass
```

The browser checks in step 12 also need `pip install -e ".[e2e]"` and `playwright install chromium`.

## 3. Choose a name prefix

Every object the scripts create starts with a prefix, so deployments can coexist. The default is `sample-agent-ret` (stacks and
objects are named `sample-agent-ret-...`). To use another prefix, pass the same value as `NamePrefix` to each stack and set
`NAME_PREFIX` to it in `.env`: the collection's data access policy only allows an index named `<prefix>-products`.
The rest of this guide uses the defaults.

## 3b. A network (only if you have no suitable VPC)

If you have no VPC with three private subnets and a NAT gateway, create a small one. It also creates a flow log for rejected
traffic, and a NAT gateway bills by the hour:

```bash
aws cloudformation deploy --stack-name sample-agent-ret-network --template-file infra/serverless/network.yaml --capabilities CAPABILITY_IAM
aws cloudformation describe-stacks --stack-name sample-agent-ret-network --query "Stacks[0].Outputs" --output table
```

`VpcId`, `VpcCidr` and `PrivateSubnetIds` are the values the next steps ask for. The template needs a Region with at least
three Availability Zones.

## 4. A host inside the VPC

You need an SSM-managed EC2 instance in the VPC to forward a port to the private endpoint. If you already have one,
note its instance ID and skip to step 5. Otherwise create a small one:

```bash
aws cloudformation deploy --stack-name sample-agent-ret-app-host --template-file infra/serverless/app-host.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides VpcId=<vpc-id> SubnetId=<one of the private subnets>
aws cloudformation describe-stacks --stack-name sample-agent-ret-app-host \
  --query "Stacks[0].Outputs" --output table
```

The instance has no inbound rules and no public address. It is reached only through Session Manager. The same host can
also run the app as a service (step 11, option B).

## 5. The collection

```bash
aws cloudformation deploy --stack-name sample-agent-ret-collection --template-file infra/serverless/collection.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides VpcId=<vpc-id> SubnetIds=<subnet-a>,<subnet-b>,<subnet-c> ClientCidr=<vpc cidr, /16 or longer> \
  DataPrincipalArns=<the role you deploy with>
```

| Parameter | Meaning |
|---|---|
| `VpcId`, `SubnetIds` | The VPC and one private subnet per Availability Zone, for the interface endpoint. |
| `ClientCidr` | The CIDR that may reach the endpoint on port 443. A prefix of /16 or longer is required (`0.0.0.0/0` is rejected). |
| `DataPrincipalArns` | IAM role ARNs that may read and write the collection through the data access policy. Use the role you run the scripts with (the role, not an assumed-role session ARN). |
| `MinSearchOcu` | Search capacity kept running. The default `2` keeps search warm. `0` lets it scale to zero, and the first query after a quiet period pays a cold start. |
| `MaxSearchOcu`, `MaxIndexingOcu` | Upper limits on capacity (default 4 each). They cap cost during the catalog load. |

Note the outputs. You need them in the next steps:

```bash
aws cloudformation describe-stacks --stack-name sample-agent-ret-collection --query "Stacks[0].Outputs" --output table
```

`CollectionEndpoint` is the collection's host name, `DataEndpointId` is its interface endpoint, and
`BedrockConnectorRoleArn` is the role OpenSearch assumes to call Bedrock. Get the endpoint's DNS name for the port
forward:

```bash
aws ec2 describe-vpc-endpoints --vpc-endpoint-ids <DataEndpointId> \
  --query "VpcEndpoints[0].DnsEntries[0].DnsName" --output text
```

## 6. Open the port forward

In a separate terminal, with the same credentials, leave this running for the rest of the setup:

```bash
scripts/port_forward.sh <instance-id> <vpce-dns-name> 9301
```

It reconnects if the session drops. If requests hang after your laptop sleeps or your login expires, stop it and start it
again.

## 7. Configure the app

```bash
cp config/.env.example .env
```

Edit `.env`: set `OS_HOST` to the `CollectionEndpoint` without `https://`, `OS_LOCAL_URL=https://localhost:9301`,
`OS_REGION` to your Region, and `BEDROCK_CONNECTOR_ROLE_ARN` to the stack output. Every variable is explained in
[configuration.md](configuration.md). Then check that the tunnel works, keeping certificate verification on: `--resolve` sends the request to the forwarded port
while naming the collection host, so the certificate is checked against the right name. A `404` means it works: the
collection answers an unsigned request to `/` with an error, which shows that the request got through.

```bash
HOST=<your OS_HOST value>
curl -s -o /dev/null -w "%{http_code}\n" --resolve "$HOST:9301:127.0.0.1" "https://$HOST:9301/"
```

## 8. Connectors, models, pipelines and the index

```bash
python infra/serverless/deploy_objects.py --dry-run     # shows what it would create
python infra/serverless/deploy_objects.py
```

This creates the Titan text embedding connector and model, the Titan multimodal connector and model (for the picture
vectors), the Cohere rerank connector and model for each rerank Region, the ingest pipeline, the hybrid and
hybrid-plus-rerank search pipelines (keyword 0.3, text vector 0.65, picture vector 0.05), and the products index, which
has a text vector field and a picture vector field. It matches objects by name and reuses what exists, except the search
pipelines, which it replaces. It is safe to run again. The created IDs are written to `config/ids.json`, which is
ignored by git.

New search pipelines can take about 15 seconds to appear. The script waits for them.

## 9. Load the catalog

The catalog is built from the public ESCI dataset, which is not distributed here. Download its two parquet files into
`data/` (the products file is about 1.1 GB):

```bash
mkdir -p data
curl -L -o data/esci_examples.parquet https://github.com/amazon-science/esci-data/raw/main/shopping_queries_dataset/shopping_queries_dataset_examples.parquet
curl -L -o data/esci_products.parquet https://github.com/amazon-science/esci-data/raw/main/shopping_queries_dataset/shopping_queries_dataset_products.parquet
```

Then load them, embedding each product's text through the ingest pipeline, and embedding the picture of each product
that has one (about 380) with Titan Multimodal Embeddings, which needs Bedrock access in `MULTIMODAL_REGION`:

```bash
python scripts/load_catalog.py --limit 200      # a quick trial: the story products come first
python scripts/load_catalog.py                  # the full set: about 165,000 products
python scripts/add_demo_product.py              # one extra demo-only product
python scripts/load_catalog.py --pictures-only  # only to add or refresh the picture vectors later
```

Loading is limited by Bedrock throughput (the default Titan v2 quota is 6,000 requests per minute), so the full set
takes an hour or more. Progress is saved to `data/catalog-done.txt`, so you can stop and run it again. If requests hang
during a long load, restart the port forward and run the loader again.

## 10. The AgentCore side

This step creates the Amazon OpenSearch Memory space (in `us-east-1`), the search Lambda, the AgentCore gateway, the
roles and the data access policy, and packages and deploys the hosted agent:

```bash
python infra/serverless/deploy_agentcore.py --no-inventory
```

Add `--app-instance <instance-id>` if the app will run on the EC2 host from step 4: the host's role is then allowed to
assume the app role (the app uses nothing else of the host's role). Without the flag you run the app under your own
credentials. See `python infra/serverless/deploy_agentcore.py --help` for all options.

It needs about ten minutes, mostly for the runtime to become ready. It writes the gateway, runtime and memory-space IDs to
`config/ids.json`.

## 11. Run the app

**Option A: on your machine.** With the port forward from step 6 still running:

```bash
streamlit run src/retail_agent_demo/app.py
```

Open <http://localhost:8501>. The app calls the collection through the port forward, the hosted agent through the
AgentCore API, and the memory space in `us-east-1`, all with your credentials.

**Option B: on the EC2 host.** The app then runs as a service as a least-privilege role, and you forward one port to it:

```bash
python scripts/deploy_app_to_ec2.py --instance <instance-id>      # once, and again after code changes
scripts/app_tunnel.sh <instance-id> 8504 8503                      # then open http://localhost:8504
```

## 12. Verify

```bash
pytest tests/unit
RUN_INTEGRATION=1 pytest tests/integration                          # your stack, through the port forward
python scripts/validate_demo.py --url http://localhost:8501        # a real browser walks the demo (needs the e2e extra)
python scripts/ui_check.py --url http://localhost:8501             # every control of the UI
python scripts/sls_reset_demo_memory.py --user bob                 # afterwards: put the demo shopper back to "Sunday"
```

The browser checks write to the demo shopper's memory, so run them when nobody else is using the app. The checks of the optional inventory server run only when `INVENTORY_MCP_ENABLED=1` is set in the shell that runs them (see the next section).

The first memory checks on a brand-new memory space can run before its first extraction has finished (about 70 seconds); if "Today with memory" or the budget test fails once, run it again.

## Optional: the inventory MCP server

The agent can also call an external MCP server (`check_stock`, `delivery_estimate`) that holds synthetic data. It is a
second AgentCore gateway with a Lambda target and Cognito machine-to-machine auth:

```bash
aws cloudformation deploy --stack-name sample-agent-ret-inventory-gateway \
  --template-file infra/cloudformation/inventory-gateway.yaml --capabilities CAPABILITY_IAM
python infra/serverless/deploy_agentcore.py        # without --no-inventory: copies its client credentials into a new secret
```

Then set `INVENTORY_MCP_ENABLED=1` and restart the app: a switch appears in the sidebar, and the tests and browser checks of the inventory server switch on with it. Do not put real inventory behind
this stack.

## Clean up

Nothing is deleted automatically. Delete in this order, and check each name before you run it on a shared account:

```bash
# the agent runtime (created by the script, not by CloudFormation) and the memory space (its memories are deleted for good).
# These two commands need a recent AWS CLI v2; if yours lacks them, do the same with boto3
# (bedrock-agentcore-control delete_agent_runtime, opensearchmemory delete_space).
aws bedrock-agentcore-control list-agent-runtimes
aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id <id from config/ids.json: agent_runtime_id>
aws opensearchmemory delete-space --region us-east-1 --space-id <id from config/ids.json: space_id>

aws cloudformation delete-stack --stack-name sample-agent-ret-agentcore
aws cloudformation delete-stack --stack-name sample-agent-ret-inventory-gateway     # if you created it
aws cloudformation delete-stack --stack-name sample-agent-ret-collection        # deletes the collection and all its data
aws cloudformation delete-stack --stack-name sample-agent-ret-app-host          # if you created it
aws cloudformation delete-stack --stack-name sample-agent-ret-network           # last: if you created it, and after the stacks above are gone

aws s3 rb s3://sample-agent-ret-code-<account-id> --force                       # the code bucket
aws secretsmanager delete-secret --secret-id sample-agent-ret/inventory-client  # if you created it
aws ssm delete-parameter --name /sample-agent-ret/app-env                       # if you used option B
```

Locally, deactivate and delete `.venv/`, `data/`, `.env` and `config/ids.json`, and stop the port forward.

## Troubleshooting

See [operations.md](operations.md#troubleshooting). The most common causes: an expired login (restart the port forward and
your credentials), a role missing from `DataPrincipalArns` (the collection answers 403; allow 30 to 60 seconds for a new
policy), and Bedrock model access not yet granted.

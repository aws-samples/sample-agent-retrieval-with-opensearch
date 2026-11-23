# Configuration reference

The app and the scripts read two things: environment variables (from `.env` at the repository root, or from your
shell) and `config/ids.json`, which the deploy scripts write. Both are ignored by git. Never commit either.

## `.env`

Copy [config/.env.example](../config/.env.example) to `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `OS_HOST` | required | The collection's host name, without `https://` (the `CollectionEndpoint` stack output). Used for TLS server name, the Host header and SigV4 signing. |
| `OS_LOCAL_URL` | `https://localhost:9200` | Where requests are sent. With the port forward from [setup.md](setup.md) this is `https://localhost:9301`. |
| `OS_REGION` | `us-east-1` | The Region of the collection, the runtime and the app. |
| `OS_AUTH` | `sigv4` | `sigv4` signs requests with your AWS credentials. `basic` exists for a user name and password but is not used by this stack. |
| `OS_SERVICE` | `aoss` | The SigV4 service name of an OpenSearch Serverless collection. |
| `OS_VERIFY_TLS` | `true` | Verify the collection's certificate, against `OS_HOST`, even though the request goes to `OS_LOCAL_URL`. |
| `NAME_PREFIX` | `sample-agent-ret` | Prefix of the objects inside the collection. It must equal the `NamePrefix` you gave the collection stack (default `sample-agent-ret`), because that stack's data access policy names the index `<prefix>-products`. |
| `RERANK_REGION` | `us-east-1` | The Region whose Bedrock serves Cohere Rerank. |
| `RERANK_MODEL_ARN` | Cohere Rerank 3.5 in `RERANK_REGION` | The rerank model. |
| `MEMORY_REGION` | your Region if it offers OpenSearch Memory, else `us-east-1` | Where `deploy_agentcore.py` creates the memory space (`us-east-1`, `us-west-2`, `ap-northeast-1` or `eu-central-1`). It is stored as `memory_region` in `config/ids.json`. |
| `MULTIMODAL_REGION` | `us-east-1` | The Region that serves Titan Multimodal Embeddings (`us-east-1` or `us-west-2`): the picture loader and the collection's multimodal connector call it there. |
| `RERANK_EXTRA_REGIONS` | empty | Comma-separated extra Regions with their own rerank connector, to spread load against the per-Region quota. Re-run `deploy_objects.py` after changing it. |
| `BEDROCK_CONNECTOR_ROLE_ARN` | required by `deploy_objects.py` | The role the collection's connectors assume (the `BedrockConnectorRoleArn` stack output). |
| `INVENTORY_MCP_ENABLED` | off | `1` shows the sidebar switch for the optional inventory MCP server. |
| `APP_STATE_DIR` | `.state` | Where the app keeps each shopper's saved outputs, so a page refresh brings them back. |
| `APP_TYPING_SECONDS` | `2.4` | How long the guided demo takes to "type" a query. `0` turns the animation off. |
| `AGENT_MODEL_ID` | Claude Haiku 4.5 | Read by the hosted agent. Set it with `deploy_agentcore.py --agent-model-id`. |

## `config/ids.json`

Written by `infra/serverless/deploy_objects.py` and `infra/serverless/deploy_agentcore.py`. The keys:

| Key | Written by | Meaning |
|---|---|---|
| `embedding_model_id`, `multimodal_model_id`, `rerank_model_id`, `rerank_lanes` | `deploy_objects.py` | The remote models (text embedding, picture embedding, rerank) and the rerank pipelines for each Region. |
| `products_index`, `ingest_pipeline`, `hybrid_pipeline`, `hybrid_rerank_pipeline` | `deploy_objects.py` | The index and pipelines inside the collection. |
| `space_id`, `memory_region` | `deploy_agentcore.py` | The OpenSearch Memory space and its Region (`us-east-1`). |
| `agent_runtime_id`, `agent_runtime_arn` | `deploy_agentcore.py` | The hosted agent. |
| `catalog_gateway_url`, `catalog_token_endpoint`, `catalog_user_pool_id`, `catalog_client_id` | `deploy_agentcore.py` | The search gateway and its Cognito client. |
| `collection_host`, `endpoint_dns`, `app_role_arn` | `deploy_agentcore.py` | Used by `deploy_app_to_ec2.py`. |

[config/ids.example.json](../config/ids.example.json) shows the shape. The files are plain JSON; edit them only if a deploy
script cannot (for example, to point the app at an index you built yourself).

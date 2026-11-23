# Operations

How to run, watch and fix the deployed sample. [setup.md](setup.md) covers deployment and clean-up.

## What runs

| Resource | Where | Notes |
|---|---|---|
| OpenSearch Serverless collection | stack `sample-agent-ret-collection` | Search capacity scales between `MinSearchOcu` and `MaxSearchOcu`; indexing capacity is capped by `MaxIndexingOcu`. |
| Interface endpoint | same stack | The only way in. Its security group takes port 443 from `ClientCidr`. |
| Search Lambda, gateway, roles, Cognito | stack `sample-agent-ret-agentcore` | The Lambda runs in the VPC with reserved concurrency of 5. |
| Hosted agent | AgentCore Runtime, created by `deploy_agentcore.py` | VPC mode, in the collection's subnets. |
| Memory space | OpenSearch Memory, `us-east-1` | Created by `deploy_agentcore.py`; CloudFormation has no resource for it. |
| App host (optional) | stack `sample-agent-ret-app-host` | systemd service `sample-agent-ret-app`, listening on `127.0.0.1:8503` only. |

## Logs and metrics

All log groups created by the templates keep 90 days.

- **Hosted agent:** `/aws/bedrock-agentcore/runtimes/<runtime name>-DEFAULT`. First place to look when the app reports a runtime error.
- **Search Lambda:** `/aws/lambda/<function name>` (the name is a stack output).
- **Traces and metrics:** the runtime role may write X-Ray segments and CloudWatch metrics in the `bedrock-agentcore` namespace.
- **Audit:** CloudTrail records the control-plane calls. Data-plane access to the collection is governed by the data access
  policy, and CloudTrail records `aoss` API calls made with your credentials.

The templates create no alarms and no dashboard. For anything beyond a sample, add alarms on the Lambda's errors and
throttles, the runtime's error rate, and the collection's search and indexing OCU usage.

## Routine tasks

| Task | How |
|---|---|
| Warm the collection after a quiet period | Send any query. With `MinSearchOcu` of `2` the first query is fast; with `0` it can take 10 to 15 seconds. |
| Warm the hosted agent | The app does this in the background when the guided demo starts. The first search in a fresh container takes about 20 seconds. |
| Put the demo shopper back to "Sunday" | `python scripts/sls_reset_demo_memory.py --user bob` (deletes that shopper's memories and events first). |
| Change the memory strategy | `aws opensearchmemory update-space` with the complete strategy list, in `us-east-1`. |
| Update the app on the host | `python scripts/deploy_app_to_ec2.py --instance <id>` (idempotent). `systemctl stop sample-agent-ret-app` on the host stops it. |
| Add a rerank Region | Add it to `RERANK_EXTRA_REGIONS` in `.env` and run `python infra/serverless/deploy_objects.py`. |
| Redeploy the agent after changing `infra/serverless/agentcore/main.py` | `python infra/serverless/deploy_agentcore.py` (it repackages and updates the runtime). |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Pipeline ... is not defined` on a search | Search pipelines share one record, and two created in quick succession can lose one. Run `deploy_objects.py` again: it creates what is missing and checks all of them. |
| Requests hang or time out after a burst of large writes | The SSM port forward wedges under sustained load. Restart `scripts/port_forward.sh`. The catalog loader uses small batches and three workers for this reason. |
| The first request after idle is slow or returns a 5xx once | The collection scales from zero when `MinSearchOcu` is `0`. Send any query to warm it; the client retries. |
| A bulk item answers `500 Internal error` | Transient. Run `load_catalog.py` again: it resends what is not in the done-file. |
| `403` from the collection | The calling role is not in `DataPrincipalArns` or in the data access policies (`<prefix>-data`, `<prefix>-data-agents`). A new policy takes 30 to 60 seconds. |
| `403 insufficient_scope` from the gateway | The Cognito token expired. The app mints a new one every few hours, so restart the app if it has been up for more than a day. |
| The runtime answers `RuntimeClientError 500` | Read the runtime log group. One cause seen: version 2 of the `mcp` package renamed its HTTP client, so the package is pinned `mcp<2`. |
| `sts:AssumeRole` denied when creating a connector | The connector role must trust `ml.opensearchservice.amazonaws.com` (the Serverless service principal). The template does. |
| Rerank answers 429 | Cohere Rerank 3.5 has a fixed per-Region quota. The app falls back to plain hybrid; add a rerank Region for load tests. |
| Memory looks stale | Extraction takes 10 to 20 seconds after the last message (the space waits 10 seconds for another message first). The "Show long-term facts" list refreshes every 10 seconds while it is open. |
| `TargetNotConnected` from a port forward | Wrong Region. Set `AWS_REGION` to the instance's Region. |

## Backup and recovery

- The **catalog** can be rebuilt from the public dataset: `scripts/load_catalog.py` is resumable.
- **Memories** are not rebuildable from public data. OpenSearch Memory keeps events for 30 days; see the service
  documentation for what it retains and exports. For anything beyond a sample, decide your own retention and export policy.
- The stacks are disposable: deployment is `aws cloudformation deploy` plus idempotent scripts, with no pipeline and no
  automatic rollback. The recovery plan is to delete and redeploy.

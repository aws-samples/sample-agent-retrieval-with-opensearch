# What this sample costs to run

This page lists what bills and how to keep it small. It gives no prices on purpose: they change, differ by Region and
depend on your agreements. Use the [AWS Pricing Calculator](https://calculator.aws/) and the pricing pages linked below, and
set a budget alarm before you deploy.

## Always on while the stacks exist

| Item | What drives the bill | Lever |
|---|---|---|
| OpenSearch Serverless collection | OpenSearch Compute Units (OCUs) for search and for indexing, plus storage. Search keeps at least `MinSearchOcu` running (default `2`: one active and one standby). | `MinSearchOcu`, `MaxSearchOcu` and `MaxIndexingOcu` in `collection.yaml`. Delete the stack when idle. [Pricing](https://aws.amazon.com/opensearch-service/pricing/) |
| Interface endpoint | Hourly charge per endpoint per Availability Zone, plus data processed. | Fewer subnets. |
| NAT gateway | Hourly charge plus data processed. The hosted agent reaches Bedrock through it. | Delete the stack when idle. VPC endpoints for Bedrock are an alternative that this sample does not set up. |
| AgentCore Runtime and Gateway | Consumption-based: compute while a session runs, and gateway requests. [Pricing](https://aws.amazon.com/bedrock/agentcore/pricing/) | Idle runtimes cost little; delete the runtime when done. |
| OpenSearch Memory space | Per the service's pricing. Check the current page. | Delete the space (this deletes its memories). |
| App host (optional) | One small EC2 instance and its volume. | Stop or delete it. |
| CloudWatch Logs | Ingestion and 90 days of storage. | Small for a sample. |
| Cognito, Secrets Manager, S3, Lambda | Small. | |

## Per use: Amazon Bedrock

| Item | Model | When |
|---|---|---|
| Embedding the catalog (once) | Titan Text Embeddings v2 | About 165,000 products, a few tens of millions of tokens in all. |
| Query embeddings | Titan Text Embeddings v2 | Every search. |
| Agent turns | Claude Haiku 4.5 | Every v2 and v3 turn; a turn can run several model calls. |
| Rerank | Cohere Rerank 3.5 | Every rerank search, and 5 to 50 candidates per query in the evaluation. |
| Memory fact extraction | Called by the OpenSearch Memory service | After each stored conversation. |

[Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) lists each model.

## Reproducing the measurements

The full evaluation (8,939 queries across seven variants) calls Bedrock for every query embedding and every rerank candidate,
and takes several hours because rerank is quota-limited per Region. A 500-query run is enough to validate
the setup, but it overstated the gain from rerank in our runs, so do not size effects with it.

## Keeping it small

- Delete the stacks when idle: it is the largest lever by far. The whole environment rebuilds from this repository.
- Leave `MinSearchOcu` at `2` while you demo, and set it to `0` between sessions if a cold first query is acceptable.
- Log groups keep 90 days (fixed in the templates, because they are the audit trail).
- Check your account's budget alarms before you load the full catalog.

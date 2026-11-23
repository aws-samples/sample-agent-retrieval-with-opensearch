# Limitations: read before reusing this project

This repository is a **sample**. It shows real capabilities with real measurements, but several deliberate shortcuts
separate it from a production system. Each item says what the sample does, why that is acceptable here, and what a
production deployment would do instead.

## Security

- **Shopper identity is a dropdown.** The app trusts the selected user as the memory `actorId`. That is the
  point of the sample: the *application*, never the model, owns identity. But nothing authenticates the person in front of
  it. Production: authenticate the user (for example with Amazon Cognito) and derive the actor server-side from the verified
  identity.
- **No Bedrock Guardrail is attached** to the agent or the connectors. The mitigations in place are a typed tool (the model
  fills in `query`, `min_price` and `max_price`; the runtime builds the query, so the model never writes query DSL) and
  recalled memory passed as delimited data with a "notes, not instructions" preamble. Production: attach a Guardrail to the
  agent and validate tool output server-side.
- **Hostile catalog text can still skew a recommendation.** The catalog is public sample data that you load yourself.
- **Memory can be poisoned by its own shopper.** Facts are written per shopper only, so the effect stays inside one shopper's
  memory, but a shopper can store a false fact about themselves.
- **A session id is not bound to a shopper.** The runtime takes the session id from the runtime context, but anyone who holds
  both a session id and permission to invoke the runtime can continue that session. Production: bind the session to the
  authenticated user.
- **The collection and the secrets use AWS-managed or AWS-owned keys.** The collection is encrypted with an AWS-owned key and
  the dead-letter queues with the AWS-managed SQS key. Use customer-managed keys where your policy requires them.
- **Log groups keep 90 days.** Security logs usually need longer retention and central storage.
- **The Cognito client secret does not rotate.** Rotate it by replacing the app client. Secrets Manager rotation is not set up.
- **Dependencies are pinned but not hash-pinned.** `pip-audit` runs in CI on every pull request and weekly, which catches known advisories only.
- **The deployment package bucket is only as private as your account.** The deployer refuses a bucket that another account
  owns and applies Block Public Access, encryption and a TLS-only policy, but anyone in your account with write access to the
  bucket can replace a package before deploy.
- **The hosted app depends on the host.** The EC2 host's role is allowed to assume the app role, and anyone with
  `ssm:SendCommand` on the host has root on it.

## Reliability and scale

- **No automatic failover for rerank.** Bedrock Rerank has a fixed per-Region quota (Cohere Rerank 3.5: 250 requests per
  minute, not adjustable). The sample can spread load across Regions and falls back to plain hybrid when rerank is throttled,
  but it has no latency budget, circuit breaker or load test. Measure before you depend on it.
- **Cold starts.** With `MinSearchOcu = 0` the first query after a quiet period takes 10 to 15 seconds, and the first search in a
  fresh agent container takes about 20 seconds.
- **Extraction is asynchronous.** New long-term facts take 10 to 20 seconds to appear after a conversation is stored.
- **OpenSearch Memory keeps no change log.** A replaced or deleted memory leaves nothing behind, so the app's memory history is
  a timeline of stored turns and created memories, not an audit.
- **Memory is in `us-east-1`** (the service exists in four Regions only), so every memory call crosses Regions from a
  collection in another one.
- **Single runs.** Timing figures and the retrieval numbers come from single runs on one account on one day.

## Functional

- **Prices and stock are synthetic** (`price_synthetic: true`), derived from the product ID, with a few story products set by
  hand. Do not benchmark price-filter relevance against real-world expectations.
- **Stock and delivery times from the inventory server are synthetic** too, deterministic from a hash of the product ID.
- **The product pictures are generated** from product titles with an image model, and are labelled illustrative in the app.
- **Picture vectors cover about 380 products.** Only the products with a generated picture have a picture vector, so the picture
  clause of the hybrid query only affects them. Its weight (0.05) comes from an earlier experiment on a larger index, not from
  a new evaluation.
- **The published retrieval scores are single runs.** They measure this configuration (hybrid with the picture clause, a window of 20, `us-east-1`) once, on one account. The picture clause's weight (0.05) comes from an earlier experiment on a larger index and has not been tuned on this evaluation. Few of the 165,000 products have a picture, so the evaluation barely exercises it.
- **The app is a demonstration UI.** It is a single Streamlit process with no authentication, rate limiting or per-user quotas.

## Operational

- The stacks create no alarms or dashboards, and deployment is scripts, not a pipeline. See [operations.md](operations.md).
- It costs money while it exists. See [cost-model.md](cost-model.md), and delete the stacks when you are done.

## Before you adapt this for production

A checklist of what the sample leaves out. None of it is wired in, and each item is a decision for you:

- [ ] Authenticate shoppers (for example with Amazon Cognito) and derive the memory actor from the verified identity.
- [ ] Attach a Bedrock Guardrail to the agent, and validate tool output on the server.
- [ ] Replace AWS-owned and AWS-managed keys with customer-managed KMS keys where your policy requires them.
- [ ] Add alarms and a dashboard (Lambda errors and throttles, runtime error rate, collection OCU use), and decide log retention and
      central storage for the audit trail.
- [ ] Set a rate limit and a per-user quota in front of the app.
- [ ] Rotate the Cognito client secret, and bind the runtime session to the signed-in user.
- [ ] Hash-pin the dependencies and keep the weekly `pip-audit` job.
- [ ] Re-run the retrieval evaluation on your own queries and judgments before you trust the fusion weights.
- [ ] Put the stacks in a deployment pipeline with review, and decide your own retention and export policy for memories.

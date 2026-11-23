# Dependency licence policy

This project is licensed under the Apache License, Version 2.0 (`LICENSE`). Its own source files carry
`# SPDX-License-Identifier: Apache-2.0` under the copyright line "Amazon.com, Inc. or its affiliates". The ESCI dataset it derives from is also
Apache-2.0 (see `NOTICE`).

## Allowed licences for dependencies

Permissive licences only: Apache-2.0, MIT, MIT-0, BSD-2-Clause, BSD-3-Clause, PSF (Python Software Foundation),
MIT-CMU, ISC. Not allowed without legal review: GPL, LGPL, AGPL, SSPL, and licences that restrict commercial use or require source disclosure of the
combined work. This repository has no package in the second group among its direct dependencies.

## Direct dependencies (checked 4 October 2026 from each package's published metadata)

| Package | Where | Licence |
|---|---|---|
| requests, boto3 | runtime (`dependencies`) | Apache-2.0 |
| python-dotenv | runtime | BSD-3-Clause |
| streamlit | app, dev | Apache-2.0 |
| pandas | data, dev | BSD-3-Clause |
| pyarrow | data, dev | Apache-2.0 |
| Pillow | app, dev | MIT-CMU |
| pytest | dev | MIT |
| cfn-lint | dev | MIT-0 |
| ruff | dev | MIT |
| playwright | e2e (browser checks) | Apache-2.0 |
| strands-agents, bedrock-agentcore | packaged into the AgentCore runtime by `infra/serverless/deploy_agentcore.py` | Apache-2.0 |
| mcp (pinned `<2`) | packaged into the AgentCore runtime | MIT |

Not checked: transitive dependencies. Before a public release, run a licence scan over the full resolved set (for example `pip-licenses` in a fresh
environment) and compare it with the allowed list above. There is no automated check in CI for this today.

## Data and documents

Prices and stock in the catalog are synthetic. Product text, product identifiers and judgments come from the Amazon Shopping Queries (ESCI) dataset
(Apache-2.0); it is not stored in this repository (`data/` is ignored), and the evaluation results under `docs/results/` are derived from it (attribution
in `NOTICE`).

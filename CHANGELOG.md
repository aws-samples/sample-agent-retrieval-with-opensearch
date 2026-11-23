# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [1.0.0]

First public release.

### Added
- A retail shopping assistant on Amazon OpenSearch Serverless, with six versions (keyword, vector, hybrid with optional rerank, agent, agent with memory, MCP) and an
  outside-MCP-client path through an Amazon Bedrock AgentCore Gateway.
- A hosted agent on Amazon Bedrock AgentCore Runtime with one typed search tool, and per-shopper long-term memory in Amazon
  OpenSearch Memory.
- Picture vectors: each generated product picture is embedded with Titan Multimodal Embeddings, and the hybrid query matches
  the shopper's words against them.
- CloudFormation for the collection, the AgentCore gateway and search Lambda, and (optionally) a VPC, an app host and an
  inventory MCP server.
- A guided demo, a retrieval evaluation on 8,939 queries, a threat model and unit, integration and browser tests.
- CI: lint, unit tests on Python 3.11 to 3.13, `cfn-lint`, `cfn_nag`, `shellcheck`, `codespell`, `pip-audit`, `gitleaks` (the pinned binary), and Dependabot.

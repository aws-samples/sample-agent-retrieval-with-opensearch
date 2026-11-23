# Generative AI in this project

This sample uses generative AI models, all through Amazon Bedrock, in three ways: inside the running demo, to make the product
pictures, and to help write the code. This page lists each use so you can judge them against your own policies.

## Models the demo calls at run time

| Model | Where it is used | What it receives |
|---|---|---|
| Anthropic Claude Haiku 4.5 | The shopping agent's turns and the query planner | The shopper's message, the tool results (product records) and, when memory is on, the shopper's remembered facts |
| Anthropic Claude Sonnet 4.5 | Permitted as the hosted agent's alternative model (`--agent-model-id`); not the default | The shopper's message and the tool results |
| Anthropic Claude Haiku 5.5, called by Amazon OpenSearch Memory | Extracts and consolidates the facts a shopper states (the model is pinned in the space; the service calls it) | The conversation turns of a made-up shopper |
| Amazon Titan Text Embeddings V2 | Embedding product text and queries for vector search | Product titles and descriptions, and query text |
| Amazon Titan Multimodal Embeddings G1 | Embedding the generated product pictures (once, at load time) and the shopper's words (at search time) into the same space | Product pictures and query text |
| Cohere Rerank 3.5 | Re-ordering the top search results by relevance | The query and the product titles |

The demo uses a public product dataset (Amazon ESCI shopping queries, Apache-2.0) and made-up shoppers. It holds no customer data,
and the inventory figures are synthetic. See [threat-model.md](threat-model.md) for the controls around the agent.

## Generated product pictures

The dataset has no pictures. About 380 small pictures in `assets/products/` were generated from product titles with
Stability AI Stable Image Core on Amazon Bedrock (`scripts/generate_product_images.py`). They are illustrative: the app labels them
that way, they do not show the real products, and they may contain errors or invented details. Do not present them as photographs of
the items.

## Help writing the code

The code and the documents were written with the help of AI coding assistants, then reviewed and tested by the author.

## What to keep in mind when you reuse this

- Model output can be wrong or incomplete. The agent's answers and the generated pictures are for demonstration only.
- Check the terms and the Regional availability of each model before you use it, and the licence terms for generated output.
- No Bedrock Guardrail is attached to the demo agent. Add one, and test it, before any wider use.
- This is sample code for non-production use. Review the generative AI use against your organisation's policies first.

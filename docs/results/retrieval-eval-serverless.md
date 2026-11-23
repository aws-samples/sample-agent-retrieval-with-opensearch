# Retrieval evaluation at the top 5 — 2026-11-22

8939 ESCI queries scored (0 failed). Relevant means rating ≥ 2 (Exact or Substitute). Intervals are 95% bootstrap confidence intervals over queries.

| variant | NDCG@5 | 95% CI | Precision@5 | MRR |
|---|---|---|---|---|
| v0 vector | 0.482 | [0.475, 0.489] | 0.505 | 0.694 |
| keyword | 0.451 | [0.444, 0.458] | 0.472 | 0.663 |
| hybrid | 0.507 | [0.501, 0.514] | 0.532 | 0.716 |
| hybrid + rerank, fetch 5 | 0.515 | [0.509, 0.522] | 0.529 | 0.739 |
| hybrid + rerank, fetch 10 | 0.539 | [0.533, 0.546] | 0.558 | 0.747 |
| hybrid + rerank, fetch 20 | 0.549 | [0.543, 0.556] | 0.567 | 0.754 |
| hybrid + rerank, fetch 50 | 0.553 | [0.547, 0.560] | 0.571 | 0.754 |

## Paired difference in NDCG@5 against hybrid

| variant | mean difference | 95% CI | better | worse | tied |
|---|---|---|---|---|---|
| v0 vector | -0.025 | [-0.028, -0.022] | 2097 | 3513 | 3329 |
| keyword | -0.056 | [-0.061, -0.052] | 2713 | 4294 | 1932 |
| hybrid + rerank, fetch 5 | +0.008 | [+0.006, +0.010] | 3056 | 2414 | 3469 |
| hybrid + rerank, fetch 10 | +0.032 | [+0.028, +0.035] | 3977 | 2634 | 2328 |
| hybrid + rerank, fetch 20 | +0.042 | [+0.038, +0.046] | 4219 | 2752 | 1968 |
| hybrid + rerank, fetch 50 | +0.046 | [+0.042, +0.051] | 4273 | 2854 | 1812 |

Judgments: ESCI US small-test labels (E=3, S=2, C=1, I=0), restricted to products in the catalog. Hybrid is keyword 0.3 + text vector 0.65 + picture vector 0.05 with min_max normalization, fetching a window of 20 and keeping the top 5. Rerank is Amazon Bedrock cohere.rerank-v3-5, called from the collection through an ML connector, scoring the product title against the shopper's query. 'fetch D' means D fused candidates are reranked and the top 5 are kept.

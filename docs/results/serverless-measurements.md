# Measured behaviour of the hosted agent, memory and rerank (17 November 2026)

One run each in a single sandbox account, a smoke test and not a benchmark. Script: `scripts/sls_measure.py`. "The T-rex bank" is
the demo's follow-the-product: a dinosaur piggy bank (product `B07QRXG93G`) that the guided demo tracks through every step (see
[guided-demo.md](../guided-demo.md)). These runs predate the picture vectors, so the rankings can differ slightly now.

## The typed search tool (hosted agent, the shopper's request, fresh sessions)

- Price range sent correctly (0 to 30 dollars): **8 of 8** runs.
- Keys other than query, min_price and max_price: none (the tool's schema allows no others).
- The T-rex bank was in the agent's search results in 8 of 8 runs.
- Median time for the whole turn (model, search, answer): 5.0 s.

## OpenSearch Memory (one prompt-based strategy with consolidation; the budget is written by the app and updated in place)

- Sunday's four turns became **4 memories** (4 shopper-facts); the first one appeared after **12 s**.
- After "my budget is 40 dollars now": 5 memories in total, 1 mention 40, **0 still say 30 without 40** (the app updated the budget memory in place).
- The change showed up after 1 s.
- There is no change log: the app's memory history is a timeline of turns stored and memories created or updated; a replaced or deleted memory leaves nothing behind.

## Rerank

- us-west-2: **works in the engine** (0.9 s; the T-rex bank is rank 1 for the rewrite, reranked)
- us-east-1: **works in the engine** (0.6 s; the T-rex bank is rank 1 for the rewrite, reranked)

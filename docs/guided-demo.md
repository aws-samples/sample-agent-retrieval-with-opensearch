# The guided demo

The app has a built-in walk-through. Open it with `?guided=1` (for example <http://localhost:8501/?guided=1>) or switch on
**Guided demo** in the sidebar, then press **Start the demo**. Each step selects the version and the shopper, types the
request and runs it. **Next** and **Back** move through the eleven steps, and the comparison table under the search bar
grows as you go, so every step can be set against the ones before it.

## The story

**Bob** is shopping for his nephew, who turns six next week and loves dinosaurs. He wants a gift that gets the boy saving
his pocket money, with a budget of 30 dollars. On Sunday he talked to the assistant and looked at a T-rex piggy bank
(product `B07QRXG93G`, $24.99, in stock) and a green dinosaur bank that was out of stock. The demo shows how the answer
improves as the retrieval gets better, and then what changes when the assistant remembers Sunday.

Bob's Sunday conversation is stored for him by `scripts/sls_reset_demo_memory.py --user bob`, which you run before each
demonstration. The prices and stock are synthetic; the T-rex bank is the product the demo follows through every step.

The shopper types, or the guided demo types for them:

| Name | Text |
|---|---|
| The request | "My nephew turns six next week and he loves dinosaurs. I'd like a gift that gets him saving his pocket money. My budget is 30 dollars." |
| The rewrite | "dinosaur piggy bank money bank kids under 30 dollars" (what a model might turn the request into) |
| Stock | "a dinosaur piggy bank under 30 dollars - is it in stock and when can it arrive?" |
| Today | "Which bank was I looking at on Sunday? Is there a green one?" |
| New budget | "My budget is 40 dollars now, my sister is chipping in. Which dinosaur piggy bank would you suggest?" |


## Act 1: The retrieval ladder

### Step 1. Lexical only: match the exact words

- **Version:** keyword   **Shopper:** bob
- **Typed:** "dinosaur piggy bank money bank kids under 30 dollars"
- **What it shows:** We start with the oldest idea: match the words exactly. No meaning, no spelling tolerance.
- **What to look at:** The T-rex bank is not in the top 5 at all: exact words miss it.

### Step 2. Vector only: match by meaning

- **Version:** v0   **Shopper:** bob
- **Typed:** "dinosaur piggy bank money bank kids under 30 dollars"
- **What it shows:** The same request, now turned into a vector and matched by meaning.
- **What to look at:** The T-rex bank appears, 4th. The arrows show who moved against keyword.

### Step 3. Lexical + vector (hybrid)

- **Version:** v1   **Shopper:** bob
- **Typed:** "dinosaur piggy bank money bank kids under 30 dollars"
- **What it shows:** Both together, fused, with the price filter inside the query.
- **What to look at:** Any arrows show who moved against vector. The T-rex bank stays 4th: keyword search never found it, so fusion has nothing to add for it.

### Step 4. Lexical + vector + rerank

- **Version:** v1+rerank   **Shopper:** bob
- **Typed:** "dinosaur piggy bank money bank kids under 30 dollars"
- **What it shows:** The fused candidates are reranked against the request.
- **What to look at:** The T-rex bank climbs into the top two (the green one of the same family sits beside it, but it is out of stock). The ladder shows the whole climb.

### Step 5. Why a prototype looks fine

- **Version:** v0   **Shopper:** bob
- **Typed:** "My nephew turns six next week and he loves dinosaurs. I'd like a gift that gets him saving his pocket money. My budget is 30 dollars."
- **What it shows:** Back to vector search, but on the shopper's own full sentence instead of the agent's rewrite.
- **What to look at:** The T-rex bank is 2nd. This is why prototypes look good: the rewrite is what hurt.


## Act 2: The agent chooses the query

### Step 6. The agent chooses the query

- **Version:** v2   **Shopper:** bob
- **Typed:** "My nephew turns six next week and he loves dinosaurs. I'd like a gift that gets him saving his pocket money. My budget is 30 dollars."
- **What it shows:** An agent plans the query and runs the search itself.
- **What to look at:** The query the agent sent, the documents it retrieved (cards, and a table marking those the answer names and where each sat in earlier results), then 'Under the hood': the plan and the search it ran.


## Act 3: Tools over MCP, both directions

### Step 7. Scenario A: an outside client calls our search

- **Version:** mcp   **Shopper:** bob
- **Typed:** "dinosaur piggy bank money bank kids under 30 dollars"
- **What it shows:** Any MCP client can reuse the same governed search with its own read-only credentials.
- **What to look at:** Under the hood: initialize, tools/list, tools/call, then five products.

### Step 8. Scenario B: our agent calls an outside tool server

- **Version:** v2   **Shopper:** bob   **Inventory server:** on
- **Typed:** "a dinosaur piggy bank under 30 dollars - is it in stock and when can it arrive?"
- **What it shows:** The agent is the MCP client of an inventory server that is not part of OpenSearch.
- **What to look at:** Under the hood: check_stock and delivery_estimate marked external.


## Act 4: Memory across sessions

### Step 9. Today, no memory

- **Version:** v2   **Shopper:** bob
- **Typed:** "Which bank was I looking at on Sunday? Is there a green one?"
- **What it shows:** Without memory the agent has never met Bob.
- **What to look at:** It opens by saying it has no record of Sunday, then searches for a green bank anyway.

### Step 10. Today, with memory

- **Version:** v3   **Shopper:** bob
- **Typed:** "Which bank was I looking at on Sunday? Is there a green one?"
- **What it shows:** Memory belongs to the shopper, not the session.
- **What to look at:** It answers from Sunday's notes: the T-rex bank, and the green one out of stock.

### Step 11. The budget changes

- **Version:** v3   **Shopper:** bob
- **Typed:** "My budget is 40 dollars now, my sister is chipping in. Which dinosaur piggy bank would you suggest?"
- **What it shows:** The $30 fact is replaced by $40, not duplicated.
- **What to look at:** Watch the facts and the history; the answer offers the dearer banks, and the table shows which retrieved documents it used.


## Before a demonstration

1. Put Bob back to Sunday: `python scripts/sls_reset_demo_memory.py --user bob`.
2. Warm the collection and the agent with a query or two. A cold start can take 10 to 20 seconds.
3. Run `python scripts/validate_demo.py --url <the app's URL>` to walk every step in a real browser (needs the `e2e` extra).
   It checks the rankings that the steps describe, so if you change the weights or the catalog it tells you which step no longer
   says what is on the screen.


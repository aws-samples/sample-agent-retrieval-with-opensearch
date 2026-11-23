# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The demo as four acts, step by step, and the lines the shopper types.

The guided mode of the app walks these steps: each one names what it shows, preloads the version, the shopper and the
request, and runs it. The scripts that drive the app (validator, recorder, UI check) import the same lines, so a wording
change happens in one place. Pure data, no Streamlit."""

from __future__ import annotations

from dataclasses import dataclass

# The shopper's lines (nobody types a person's name; the shopper is the logged-in user).
REQUEST = ("My nephew turns six next week and he loves dinosaurs. I'd like a gift that gets him saving his pocket money. "
           "My budget is 30 dollars.")
REWRITE = "dinosaur piggy bank money bank kids under 30 dollars"
STOCK = "a dinosaur piggy bank under 30 dollars - is it in stock and when can it arrive?"
TODAY = "Which bank was I looking at on Sunday? Is there a green one?"
BUDGET = "My budget is 40 dollars now, my sister is chipping in. Which dinosaur piggy bank would you suggest?"
FORGET_LINE = "My budget is 30 dollars. Any dinosaur piggy banks?"
TARGET = "B07QRXG93G"  # the T-rex bank the ladder follows

# The scripted Sunday conversation, stored for Bob by the reset scripts.
SUNDAY = [
    {"role": "user", "text": ("Hi, I'm Bob. My nephew Sam turns six next week and he's obsessed with "
                              "dinosaurs. I'd like something that gets him saving his pocket money. "
                              "My budget is 30 dollars.")},
    {"role": "assistant", "text": ("The MASSJOY Dinosaur Piggy Bank (Tyrannosaurus Rex, product B07QRXG93G) "
                                   "is $24.99 and in stock. There's also a green dinosaur piggy bank from "
                                   "WAIT FLY for $27.99, but it's out of stock.")},
    {"role": "user", "text": ("The T-rex one looks great. Would it make a good birthday present for him? "
                              "I'll come back later this week to buy it.")},
    {"role": "assistant", "text": ("Yes — it's a sturdy resin piggy bank sized for a six-year-old, and it "
                                   "makes saving pocket money fun. At $24.99 it fits your $30 budget.")},
]


ACT_TITLES = {1: "The retrieval ladder", 2: "The agent chooses the query", 3: "Tools over MCP, both directions",
              4: "Memory across sessions"}


@dataclass(frozen=True)
class Step:
    act: int
    title: str
    version: str  # the kind to select: keyword, v0, v1, v1+rerank, v2, mcp or v3 (turns.VERSION_LABELS names them)
    query: str  # typed for the person demonstrating and run; "" means there is nothing to run (a button to click)
    says: str  # what this step shows, in one line
    look: str  # what to look at on the screen
    shopper: str = "bob"
    inventory: bool = False
    new_session: bool = False  # start a fresh agent conversation first
    lists: bool = False  # open the memory lists
    alt_says: str = ""  # said instead of `says` when the steps before this one in its act were not run (the demo opens at START)
    alt_look: str = ""  # the same for `look`


STEPS: tuple[Step, ...] = (
    Step(1, "Lexical only: match the exact words", "keyword", REWRITE,
         "We start with the oldest idea: match the words exactly. No meaning, no spelling tolerance.",
         "The T-rex bank is not in the top 5 at all: exact words miss it."),
    Step(1, "Vector only: match by meaning", "v0", REWRITE,
         "The same request, now turned into a vector and matched by meaning.",
         "The T-rex bank appears, 4th. The arrows show who moved against keyword."),
    Step(1, "Lexical + vector (hybrid)", "v1", REWRITE,
         "Both together, fused, with the price filter inside the query.",
         "Any arrows show who moved against vector. The T-rex bank stays 4th: keyword search never found it, so fusion has nothing to add for it.",
         alt_says="Lexical and vector search together, fused, with the price filter inside the query.",
         alt_look="The T-rex bank stays 4th: keyword search never found it, so fusion has nothing to add for it. The price filter now sits inside the query."),
    Step(1, "Lexical + vector + rerank", "v1+rerank", REWRITE,
         "The fused candidates are reranked against the request.",
         "The T-rex bank climbs into the top two (the green one of the same family sits beside it, but it is out of stock). The ladder shows the whole climb.",
         alt_look="The T-rex bank climbs into the top two (the green one of the same family sits beside it, but it is out of stock). The ladder shows the climb from hybrid to rerank."),
    Step(1, "Why a prototype looks fine", "v0", REQUEST,
         "Back to vector search, but on the shopper's own full sentence instead of the agent's rewrite.",
         "The T-rex bank is 2nd. This is why prototypes look good: the rewrite is what hurt.",
         alt_look="The T-rex bank is 2nd: even plain vector search does well on the shopper's own sentence. This is why prototypes look good; the model's rewrite is what hurts."),
    Step(2, "The agent chooses the query", "v2", REQUEST,
         "An agent plans the query and runs the search itself.",
         "The query the agent sent, the documents it retrieved (cards, and a table marking those the answer names and where each sat in earlier results), then 'Under the hood': the plan and the search it ran."),
    Step(3, "Scenario A: an outside client calls our search", "mcp", REWRITE,
         "Any MCP client can reuse the same governed search with its own read-only credentials.",
         "Under the hood: initialize, tools/list, tools/call, then five products."),
    Step(3, "Scenario B: our agent calls an outside tool server", "v2", STOCK,
         "The agent is the MCP client of an inventory server that is not part of OpenSearch.",
         "Under the hood: check_stock and delivery_estimate marked external.", inventory=True, new_session=True),
    Step(4, "Today, no memory", "v2", TODAY,
         "Without memory the agent has never met Bob.",
         "It opens by saying it has no record of Sunday, then searches for a green bank anyway.", new_session=True),
    Step(4, "Today, with memory", "v3", TODAY,
         "Memory belongs to the shopper, not the session.",
         "It answers from Sunday's notes: the T-rex bank, and the green one out of stock.", new_session=True),
    Step(4, "The budget changes", "v3", BUDGET,
         "The $30 fact is replaced by $40, not duplicated.",
         "Watch the facts and the history; the answer offers the dearer banks, and the table shows which retrieved documents it used.", lists=True),
)


# The column each step gets in the running comparison table (what every step returned, side by side).
SHORT = ("Lexical", "Vector", "Lexical + vector", "+ Rerank", "Vector, full sentence", "Agent", "MCP: outside client",
         "Agent + inventory", "Agent, no memory", "Agent + memory", "Memory, $40 budget")


# The sidebar menu of the guided demo: all eleven steps in the order they run, so the selection walks down the list.
MENU = ("Lexical only", "Vector only", "Lexical + vector (hybrid)", "Lexical + vector + rerank", "Vector only, the full sentence",
        "Agent (hybrid search), no memory", "MCP: outside client (hybrid search)", "Agent + inventory MCP server",
        "Agent, no memory: Today", "Agent + memory: Today", "Agent + memory: the budget changes")


def menu() -> list[str]:
    """All eleven entries, numbered by their place in STEPS."""
    return [f"{i + 1}. {name}" for i, name in enumerate(MENU)]


# The demo opens here: Start goes to step 3 (hybrid). Steps 1 and 2 (lexical, vector) stay on the screen, in the sidebar list and
# behind Back, so they can be shown when the room asks.
START = 2


def first() -> int:
    """Index of the step the Start button opens."""
    return START


def _unrun_before(i: int, ran: set[int] | None) -> bool:
    """True when the demo was opened after the beginning of this step's act, so earlier rungs of its ladder were not run."""
    return ran is not None and any(j not in ran for j in range(i) if STEPS[j].act == STEPS[i].act)


def says(i: int, ran: set[int] | None = None) -> str:
    """What to say at step i. `ran` is the set of steps already run in this guided run; None means the full text."""
    return STEPS[i].alt_says if STEPS[i].alt_says and _unrun_before(i, ran) else STEPS[i].says


def look(i: int, ran: set[int] | None = None) -> str:
    return STEPS[i].alt_look if STEPS[i].alt_look and _unrun_before(i, ran) else STEPS[i].look


def short(i: int) -> str:
    return SHORT[i]


def acts() -> list[int]:
    return sorted({s.act for s in STEPS})


def position(i: int) -> tuple[int, int, int, int]:
    """(act, step within the act, steps in the act, step number overall) for the step at index i."""
    s = STEPS[i]
    in_act = [j for j, t in enumerate(STEPS) if t.act == s.act]
    return s.act, in_act.index(i) + 1, len(in_act), i + 1

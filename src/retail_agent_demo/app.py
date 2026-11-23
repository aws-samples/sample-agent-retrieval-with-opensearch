# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Sample demo app: a shopping search bar over seven retrieval and agent versions, live.

Run: streamlit run src/retail_agent_demo/app.py
"""

from __future__ import annotations

import html
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import boto3
import streamlit as st
from botocore.config import Config

from retail_agent_demo import acts, agent_results, explain, inventory_table, ranks, search, timing, ui_tables
from retail_agent_demo import agentcore as ac
from retail_agent_demo import mcp_client as mcp_mod
from retail_agent_demo import osmemory as osm
from retail_agent_demo import turns as turns_mod
from retail_agent_demo.budget import parse_budget
from retail_agent_demo.config import load_settings
from retail_agent_demo.images import image_for
from retail_agent_demo.os_client import OSClient
from retail_agent_demo.turn_store import TurnStore
from retail_agent_demo.waits import pause

# agent_memory_agent records which agent agent_memory_id belongs to: a session id is only valid for the agent that made it.
SESSION_KEYS = ("agent_memory_id", "agent_memory_agent", "app_session_id", "recalled", "forgot")
# what belongs to one shopper (saved outputs included) and must not reach the next; "New session" keeps the outputs.
# The outputs themselves are also kept on disk (turn_store.py), so a page refresh brings them back for the same shopper.
SHOPPER_KEYS = SESSION_KEYS + ("turns",)
HISTORY_SHOWN = 8
BACKEND = "serverless"  # the key the saved outputs are filed under (turn_store.py)
SHOWN = 6  # rows of facts / history the sidebar lists; the newest are the ones worth seeing


def _clip(text: str, limit: int = 110) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


VERSIONS = turns_mod.VERSIONS  # explicit names: "Lexical only", "Vector only", "Lexical + vector (hybrid)", ...
TYPING_SECONDS = float(os.environ.get("APP_TYPING_SECONDS", "2.4"))  # how long the guided demo takes to "type" a query


def is_agent(version: str) -> bool:
    return turns_mod.version_kind(version) in turns_mod.AGENT_KINDS


@st.cache_resource
def get_client() -> OSClient:
    return OSClient(load_settings())


@st.cache_resource
def get_agentcore_client():
    """boto3 client for AgentCore Runtime invocations."""
    return boto3.client("bedrock-agentcore", region_name=load_settings().region,
                        config=Config(read_timeout=180, retries={"max_attempts": 2}))


@st.cache_resource
def get_memory_client():
    """boto3 client for Amazon OpenSearch Memory. The service is in a few Regions only, so the space's
    Region (ids `memory_region`, us-east-1 by default) can differ from the app's."""
    return osm.new_client(load_settings().ids.get("memory_region", "us-east-1"))


FORGET_SETTLE_S = 30  # after a forget, keep watching this long for an extraction that was already under way (idle timeout 10 s + extraction)
GATEWAY_TOKEN_TTL_S = 6 * 3600  # a Cognito token lives 24 h; a long-running app must mint a new one well before that


@st.cache_resource(ttl=GATEWAY_TOKEN_TTL_S)
def get_gateway_client() -> ac.GatewayHttp:
    """An outside client of the catalog-search Gateway: it mints its own Cognito token (client credentials).

    The token is cached for a few hours only. An app that has been up for more than a day would otherwise keep sending an
    expired token, which the Gateway answers with 403 insufficient_scope."""
    s = load_settings()
    pool, client_id = s.id("catalog_user_pool_id"), s.id("catalog_client_id")
    secret = boto3.client("cognito-idp", region_name=s.region).describe_user_pool_client(
        UserPoolId=pool, ClientId=client_id)["UserPoolClient"]["ClientSecret"]
    token = ac.cognito_token(s.id("catalog_token_endpoint"), client_id, secret, "catalog/search")
    return ac.GatewayHttp(s.id("catalog_gateway_url"), token)


def md(text: str) -> str:
    """Markdown treats $...$ as math, so a reply with two prices would turn the text between them into a formula.
    The model sometimes escapes the dollar signs itself; undo that first so they are not escaped twice."""
    return text.replace("\\$", "$").replace("$", "\\$")


def _badge(move: str) -> str:
    if move.startswith("▲"):
        return f":green[{move}]"
    if move.startswith("▼"):
        return f":red[{move}]"
    return ":blue[new]" if move == "new" else (":gray[=]" if move else "")


def render_cards(rows: list[dict], key: str) -> None:
    """The results as a row of product cards: picture (or a neutral tile), rank, how far it moved, title, price, stock."""
    if not rows:
        return
    for col, row in zip(st.columns(len(rows)), rows, strict=True):
        target = "-target" if row.get("id") == acts.TARGET else ""  # the demo's T-rex bank: its card gets the colour of its grid row
        with col, st.container(border=True, key=f"card-{key}-{row['#']}{target}"):
            picture = (row.get("image") if row.get("image") and Path(row["image"]).exists() else None) or (
                image_for(row["id"]) if row.get("id") else None)  # by product id, so results saved earlier show it too
            if picture:
                st.image(picture, use_container_width=True)
            else:
                st.markdown("<div style='height:110px;background:#eef0f3;border-radius:6px;display:flex;align-items:center;"
                            "justify-content:center;color:#8b93a1;font-size:12px'>no picture</div>", unsafe_allow_html=True)
            st.markdown(f"**#{row['#']}** {_badge(row.get('move', ''))}" + (" :green[✓ in the answer]" if row.get("used") else ""))
            st.markdown(f"**{md(_clip(row['product'], 64))}**")
            st.markdown(f"{md(row['price'])} · " + (":green[in stock]" if row["in stock"] == "✅" else ":red[out of stock]"))


def query_line(label: str, text: str) -> None:
    """The words that were sent to the search, larger and bold so they are easy to read from the back of a room."""
    st.markdown(f"<div style='font-size:1.2rem;line-height:1.5'><span style='color:#5f6b7a'>{html.escape(label)}</span> "
                f"<b>{html.escape(text)}</b></div>", unsafe_allow_html=True)


def cell(text: str) -> str:
    """A table cell: dollar signs escaped, and a '|' in a product title (some have one) must not split the row."""
    return md(str(text)).replace("|", "/")


def render_agent_table(turn: dict) -> None:
    """The documents the agent's search returned: its rank, whether the answer names it, and where the same product sat in
    earlier results (the agent writes its answer from these). The product the demo follows is bold on light green."""
    rows = [r for r in turn["products"] if "used" in r]
    if not rows:
        return
    st.markdown("**Documents the agent retrieved** — the answer is written from these")
    body = [{"cells": [r["#"], r["product"][:60], r["price"], "in stock" if r["in stock"] == "✅" else "out of stock",
                       "✓ yes" if r["used"] else "–", r.get("earlier") or "–"], "highlight": r.get("id") == acts.TARGET} for r in rows]
    st.markdown(ui_tables.table(["#", "product", "price", "stock", "in the answer", "earlier results"], body), unsafe_allow_html=True)


def render_inventory_table(turn: dict) -> None:
    """What the external inventory server answered for the products the agent asked about (check_stock, delivery_estimate)."""
    found = inventory_table.rows(turn.get("steps"), turn.get("products"))
    if not found:
        return
    st.markdown("**Inventory server answers** — outside OpenSearch, from the external MCP server")
    body = [{"cells": [r["product"][:60], r["stock"], r["warehouse"], r["days"]], "highlight": r["id"] == acts.TARGET} for r in found]
    st.markdown(ui_tables.table(["product", "stock", "warehouse", "delivery"], body), unsafe_allow_html=True)


def render_timing(turn: dict) -> None:
    """How long the request took in all and where the time went: the steps of the request, and the parts of a step indented under it."""
    info = turn.get("timing")
    if not info or not info.get("segments"):
        return
    total = info["total"]
    st.markdown(f"**Took {timing.seconds_text(total)}** — where the time went")
    body = [{"cells": [("↳ " if seg["level"] else "") + seg["name"], timing.seconds_text(seg["seconds"]),
                       timing.bar(seg["seconds"], total) if not seg["level"] else timing.bar(seg["seconds"], total) + " (inside the step above)"]}
            for seg in info["segments"]]
    st.markdown(ui_tables.table(["step", "time", "share of the total"], body), unsafe_allow_html=True)


def render_comparison(saved: list[dict]) -> None:
    """The guided demo's running comparison: what every step so far returned, side by side. It grows step by step, so each step
    builds on the one before; the T-rex bank is bold on light green."""
    columns, rows = ranks.comparison(saved, st.session_state.get("guide_run"))
    if not columns:
        return
    st.markdown("**Comparison so far** — where each document sat in every step (position 1 is the top of the page)")
    for name, turn in ranks.silent_steps(saved, st.session_state.get("guide_run")):
        st.caption(f"**{md(name)}** has no ranking: {md(inventory_table.why_no_search(turn) or '')}")
    body = [{"cells": [r["title"][:52], *r["cells"]], "highlight": r["id"] == acts.TARGET} for r in rows]
    st.markdown(ui_tables.table(["product", *columns], body), unsafe_allow_html=True)


def render_ladder(history: list[dict], turn: dict) -> None:
    """Where each product sat on every rung run so far for this request: keyword, vector, hybrid, rerank."""
    kinds, rows = ranks.ladder_table(history, turn["prompt"])
    if len(kinds) < 2 or turns_mod.version_kind(turn["version"]) not in ranks.LADDER:
        return
    st.markdown("**Rank ladder** — position 1 is the top of the page")
    body = [{"cells": [r["title"][:48], *[f"#{r['pos'][k]}" if r["pos"][k] else "–" for k in kinds]], "highlight": r["id"] == acts.TARGET}
            for r in rows]
    st.markdown(ui_tables.table(["product", *[ranks.NAMES[k] for k in kinds]], body), unsafe_allow_html=True)


def render_hood(turn: dict) -> None:
    """'Under the hood': the request the version sent, or the agent's thinking, apart from the results."""
    st.markdown("**Under the hood**")
    items = turn.get("hood") or []
    if turn.get("trace"):
        st.markdown("**MCP protocol steps**")
    first = next((i for i, item in enumerate(items) if item.get("body")), None)  # the main request is open, the rest fold
    if turn.get("trace"):  # the MCP exchange: the search call is the one worth opening
        first = max((i for i, item in enumerate(items) if item.get("body")), default=None)
    for i, item in enumerate(items):
        st.markdown(f"**{i + 1}. {md(item['title'])}**")
        if item.get("detail"):
            st.caption(md(item["detail"]))
        if item.get("body"):
            st.json(item["body"], expanded=(i == first))
    if not items and turn.get("steps"):  # a turn saved before this panel existed
        for i, step in enumerate(turn["steps"], 1):
            where = f" — external MCP server: {step['external']}" if step.get("external") else ""
            st.markdown(f"**{i}. `{step['tool']}`**{where}")
            st.caption(f"in: {md(step['input'])}")
            st.caption(f"out: {md(step['output'])}")
    if not items and not turn.get("steps"):
        st.caption("Nothing to show for this version.")


def render_turn(turn: dict, expanded: bool, history: list[dict]) -> None:
    """One saved output in a fold. The newest is open; older ones stay closed and can be opened to compare."""
    with st.expander(turns_mod.fold_label(turn), expanded=expanded):
        agent = is_agent(turn["version"])
        query_line("You asked:" if agent else "You searched for:", turn["prompt"])
        searched = turn.get("searched")
        if searched and searched.get("query"):
            limits = ""
            if searched.get("hi") not in (None, 100000, 100000.0):
                limits = f"  (price ${searched.get('lo') or 0:g} to ${searched['hi']:g})"
            query_line("The agent searched for:", searched["query"] + limits)
        st.markdown(md(turn["answer"]))
        render_cards(turn["products"], str(turn["n"]))  # full width, so a title is readable
        render_agent_table(turn)
        render_inventory_table(turn)
        reason = inventory_table.why_no_search(turn) if agent else None
        if reason:
            st.caption(f"No search ranking for this answer: {md(reason)}")
        for note in turn["notes"]:
            st.caption(note)
        render_timing(turn)
        render_ladder(history, turn)  # full width: its columns are the rungs
        render_hood(turn)


def get_store() -> TurnStore:
    return TurnStore()  # cheap, and reads APP_STATE_DIR each time, so a cached copy cannot point at a stale directory


def _clear_history(backend: str, user_id: str) -> None:
    """Button callback: forget the saved outputs on screen and on disk (the agent's memory is not touched)."""
    st.session_state["turns"] = []
    get_store().clear(backend, user_id)


def _history_lines(rows: list[dict]) -> list[str]:
    out = []
    for row in rows[:HISTORY_SHOWN]:
        when = row["when"].astimezone().strftime("%H:%M:%S") if row.get("when") else ""
        out.append(f"- **{row['action']}** {when} — {md(_clip(row['detail']))}")
    return out


@st.fragment(run_every="10s")
def memory_panel() -> None:
    """The sidebar's memory section. The two switches open and close the lists; while a list is open it re-reads the
    shopper's memory every 10 seconds (extraction is asynchronous, so facts appear a while after a turn), and
    'Refresh now' reads it at once. It runs as a fragment, so switching or refreshing does not re-run the whole page."""
    s = get_client().s
    user_id = st.session_state.get("user_id", "bob")
    st.header("Memory")
    show_facts = st.toggle("Show long-term facts", key="show_facts",
                           help="Lists what is remembered about this shopper. Stays open and refreshes every 10 s.")
    show_history = st.toggle("Show memory history", key="show_history",
                             help="A timeline of the turns stored and the memories created or updated. OpenSearch Memory keeps "
                                "no change log, so replaced or deleted memories do not appear.")
    try:
        if show_facts:
            facts = osm.list_facts(get_memory_client(), s.id("space_id"), user_id)
            st.markdown("**Long-term facts**" + (f" ({len(facts)})" if facts else ""))
            for f in facts[:SHOWN]:  # newest first
                st.markdown(f"- *{f['strategy_type']}* — {md(_clip(f['memory']))}")
            if not facts:
                st.caption("Nothing is remembered about this shopper yet.")
            elif len(facts) > SHOWN:
                st.caption(f"newest {SHOWN} of {len(facts)}")
        if show_history:
            rows = osm.list_activity(get_memory_client(), s.id("space_id"), user_id)
            st.markdown("**Memory history**" + (f" ({len(rows)})" if rows else ""))
            lines = _history_lines(rows)
            for line in lines:
                st.markdown(line)
            if not rows:
                st.caption("No history yet.")
            elif len(rows) > HISTORY_SHOWN:
                st.caption(f"newest {HISTORY_SHOWN} of {len(rows)}")
    except Exception as e:  # noqa: BLE001 - a memory read must never break the page; say what happened and let the user retry
        st.caption(f"Could not read memory just now ({type(e).__name__}); it will retry.")
    if show_facts or show_history:
        st.caption(f"Updated {datetime.now().strftime('%H:%M:%S')} · refreshes every 10 s")
        st.button("Refresh now", key="refresh_memory")
    if st.button("⚠️ Forget this shopper (memories and events)"):
        with st.spinner("Forgetting, then watching for memories still being extracted…"):
            result = osm.forget_shopper_verified(get_memory_client(), s.id("space_id"), user_id, settle=FORGET_SETTLE_S)
        for k in SHOPPER_KEYS:  # otherwise the forgotten facts are recalled and stored again
            st.session_state.pop(k, None)
        get_store().clear(BACKEND, user_id)
        st.session_state["forgot"] = result
        st.rerun()  # the whole page, so the saved outputs on screen go too
    if st.session_state.get("forgot"):
        st.json(st.session_state["forgot"])


def _warm_hosted_agent() -> None:
    """Have the hosted agent run one small search in the background, so a step does not pay for the cold start of
    its first search (about 20 s in a fresh container). Skips itself when it ran a few minutes ago."""
    s = get_client().s
    if not s.ids.get("agent_runtime_arn"):
        return
    threading.Thread(target=ac.warm_up, args=(get_agentcore_client(), s.id("agent_runtime_arn")), daemon=True).start()


def _go_step(i: int, run: bool = True) -> None:  # Next and Back both run the step they land on
    """Button callback of the guided demo: select the step's version and shopper, set the switches, fill the search bar."""
    step = acts.STEPS[i]
    if st.session_state.get("step", -1) < 0 or "guide_run" not in st.session_state:  # a new guided run: its comparison starts empty
        st.session_state["guide_run"] = time.time()
        st.session_state["guide_log"] = []
    st.session_state["step"] = i
    st.session_state["version"] = st.session_state["_version"] = turns_mod.VERSION_LABELS[step.version]
    st.session_state["step_menu"] = acts.menu()[i]  # the sidebar list follows the step
    st.session_state["user_id"] = step.shopper
    st.session_state["use_inventory_mcp"] = step.inventory
    st.session_state["show_facts"] = st.session_state["show_history"] = step.lists
    if step.new_session:
        for k in SESSION_KEYS:
            st.session_state.pop(k, None)
    st.session_state["query"] = step.query
    st.session_state["_autorun"] = run and bool(step.query)
    _warm_hosted_agent()


def _pick_step() -> None:  # on_change of the guided sidebar list
    chosen = st.session_state.get("step_menu")
    if chosen in acts.menu():
        _go_step(acts.menu().index(chosen))


def guided_banner() -> None:
    """The guided demo's header: which act and step this is, what it shows, what to look at, and Back / Next."""
    i = st.session_state.get("step", -1)
    with st.container(border=True, key="guide"):
        if i < 0:
            st.markdown("**Guided demo** — four acts: " + " · ".join(
                f"{a}. {acts.ACT_TITLES[a]}" for a in acts.acts()))
            st.button("Start the demo", key="guide_start", on_click=_go_step, args=(acts.first(),), type="primary")
            return
        act, k, n, overall = acts.position(i)
        step = acts.STEPS[i]
        text, back, nxt = st.columns([8, 1, 1])
        with text:
            st.markdown(f"**Act {act} of {len(acts.acts())} · {acts.ACT_TITLES[act]}** — step {k} of {n} "
                        f"(overall {overall} of {len(acts.STEPS)})")
            st.markdown(f"#### {md(step.title)}")
            ran = {t.get("step") for t in st.session_state.get("guide_log", [])}  # the earlier steps of this run: the words follow what was shown
            st.markdown(md(acts.says(i, ran)))
            st.caption(f"Look for: {md(acts.look(i, ran))}")
        back.button("◀ Back", key="guide_back", disabled=i == 0, on_click=_go_step, args=(i - 1,),
                    use_container_width=True)
        nxt.button("Next ▶", key="guide_next", disabled=i == len(acts.STEPS) - 1, on_click=_go_step, args=(i + 1,),
                   type="primary", use_container_width=True)


def main():
    st.set_page_config(page_title="Sample — Bob's shopping assistant", layout="wide")
    client = get_client()
    s = client.s
    if "guided" not in st.session_state:
        st.session_state["guided"] = st.query_params.get("guided") == "1"
        start = st.query_params.get("start", "")  # ?guided=1&start=5 opens the demo at step 6 (used by the recorder)
        if start.isdigit() and int(start) < len(acts.STEPS):
            st.session_state["guided"] = True
            _go_step(int(start))

    # ---------------- sidebar: identity + version switch -------------------
    with st.sidebar:
        st.header("Shopper")
        user_id = st.selectbox("Logged in as", ["bob", "demo-shopper"], key="user_id")
        # Everything cached for one shopper (recalled facts, agent session, saved results) must not reach the next.
        if st.session_state.get("active_user") != user_id:
            for k in SHOPPER_KEYS:
                st.session_state.pop(k, None)
            st.session_state["active_user"] = user_id
        if st.session_state["guided"]:
            # all eleven steps in the order they run: the selection walks down the list; a click jumps to that step and runs it
            st.radio("Assistant version", acts.menu(), index=None, key="step_menu", on_change=_pick_step)
            i = st.session_state.get("step", -1)
            version = turns_mod.VERSION_LABELS[acts.STEPS[i].version] if i >= 0 else st.session_state.get("_version", VERSIONS[0])
        else:
            if "version" not in st.session_state:  # coming back from the guided demo: keep the version it ended on
                st.session_state["version"] = st.session_state.get("_version", VERSIONS[0])
            version = st.radio("Assistant version", VERSIONS, key="version")
            st.session_state["_version"] = version
        use_mcp_agent = False
        # Off unless INVENTORY_MCP_ENABLED=1, so the switch stays out of the way until someone opts in.
        if (os.environ.get("INVENTORY_MCP_ENABLED") == "1" and s.ids.get("agent_runtime_arn")
                and is_agent(version)):
            use_mcp_agent = st.checkbox(
                "Agent also calls the inventory MCP server", key="use_inventory_mcp",
                help="Scenario B: the agent is the MCP CLIENT. It also calls an inventory server outside OpenSearch "
                     "(check_stock, delivery_estimate) and marks those steps 'external MCP server'. Switching starts "
                     "a new agent session. The agent uses them when you ask about stock or delivery.")
            if use_mcp_agent:
                st.caption("Ask about stock or delivery to see the external calls.")
        st.caption("**OpenSearch Serverless** for retrieval, **Bedrock AgentCore** for the agent and MCP, **OpenSearch Memory** for memory.")
        st.toggle("Guided demo", key="guided", help="Walk the four acts step by step. Each step selects the version and "
                                                    "the shopper, types the request and runs it.")

        st.divider()
        memory_panel()
        st.divider()
        if st.button("New session", help="Start a new agent conversation. Your saved results stay on the page."):
            for k in SESSION_KEYS:
                st.session_state.pop(k, None)
        clear_slot = st.empty()  # filled at the end of the run, so it reflects the results this run just added

    backend = BACKEND

    def draw_clear_button() -> None:
        clear_slot.button("Clear results", disabled=not st.session_state.get("turns"),
                          help="Delete the saved results for this shopper, on the page and on the server. "
                               "The agent's memory is not touched.",
                          on_click=_clear_history, args=(backend, user_id))

    # ---------------- main: the search bar, then the saved results, newest first ---------------------
    st.markdown("<style>"
                f"[class*='st-key-card-'][class*='-target']{{background:{ui_tables.HIGHLIGHT};}}"  # the T-rex card, as in the grid
                "[data-testid='stForm']{background:#e3f0ff;border:3px solid #0972d3;border-radius:12px;padding:14px 16px 6px 16px;}"
                ".st-key-query input{font-size:1.25rem !important;font-weight:700 !important;background:#ffffff !important;"
                "border:1px solid #0972d3 !important;}"
                "</style>", unsafe_allow_html=True)  # a framed search bar, so it is obvious; the text is bigger and bold
    st.subheader("Bob's shopping assistant")
    if st.session_state["guided"]:
        guided_banner()
    agent_mode = is_agent(version)
    bar = st.empty()
    if st.session_state["guided"] and st.session_state.get("_autorun") and st.session_state.get("query") and TYPING_SECONDS > 0:
        text = st.session_state["query"]  # the guided demo "types" the query before it runs it
        delay = min(0.03, TYPING_SECONDS / len(text))
        for n in range(1, len(text) + 1):
            bar.markdown(ui_tables.typing_box(text[:n]), unsafe_allow_html=True)
            pause(delay)
        pause(0.4)
        bar.markdown(ui_tables.typing_box(text, caret=False, pressed=True), unsafe_allow_html=True)  # the Search button is pressed
        pause(0.6)
    with bar.container(), st.form("search", border=False):
        box, go = st.columns([9, 1])
        typed = box.text_input("Search", key="query", label_visibility="collapsed",
                               placeholder="Ask the shopping assistant…" if agent_mode else "Search products…")
        pressed = go.form_submit_button("Search", key="search_go", use_container_width=True, type="primary")
    prompt = typed.strip() if (pressed or st.session_state.pop("_autorun", False)) else ""
    st.caption(f"**{version}** · every result is kept in a fold below (newest first), with the settings that produced it, "
               "and survives a page refresh.")

    if "turns" not in st.session_state:  # a new browser session (or a refresh): bring this shopper's results back
        st.session_state["turns"] = get_store().load(backend, user_id)
    saved = st.session_state["turns"]
    if prompt:
        saved.append(run_search(client, s, version, use_mcp_agent, user_id, prompt, saved))
        get_store().save(backend, user_id, saved)
        if saved[-1].get("step") is not None:  # the guided run's own log: it survives a change of shopper and of agent session
            st.session_state.setdefault("guide_log", []).append(saved[-1])
    if st.session_state["guided"]:
        render_comparison(st.session_state.get("guide_log", []))
    for k, turn in enumerate(reversed(saved)):
        idx = len(saved) - 1 - k
        render_turn(turn, expanded=(k == 0), history=saved[: idx + 1])
    draw_clear_button()


def run_search(client, s, version: str, use_mcp_agent: bool, user_id: str, prompt: str,
               saved: list[dict]) -> dict:
    """Run one request on the selected version and return the saved turn (answer, cards, 'under the hood')."""
    rv = search.retrieval_from(s.ids)  # the first index, or the second one (three vector fields) once it has been built
    index = rv.index
    emb = s.id("embedding_model_id")
    tl = timing.Timeline()
    lo, hi = parse_budget(prompt)
    products, steps, trace, notes, recalled, fell_back, hood = [], [], [], [], 0, False, []
    kind = turns_mod.version_kind(version)
    guided_step = st.session_state.get("step") if st.session_state.get("guided") and st.session_state.get("step", -1) >= 0 else None

    def plain_hybrid():
        return search.run_v1_inline(client, index, emb, prompt, lo, hi, pipeline=rv.pipeline,
                                    vector_fields=rv.vector_fields, image_model_id=rv.image_model_id)

    with st.spinner("Working…"):
        if kind == "keyword":
            with tl.span("Lexical search (BM25 over the text fields)"):
                products = search.run_lexical(client, index, prompt, lo, hi)
            answer = "Keyword search: the words you typed, matched exactly:"

        elif kind == "v0":
            with tl.span("Vector search (embed the query, k-NN)"):
                products = search.run_v0(client, index, emb, prompt, lo, hi)
            answer = "Here's what I found (vector search, price checked afterwards):"

        elif kind == "v1":
            with tl.span("Hybrid search (embed, BM25 + k-NN, fuse)"):
                products = plain_hybrid()
            answer = "Hybrid search with the price filter inside the query:"

        elif kind == "v1+rerank":
            try:
                with tl.span("Hybrid search + rerank (inside the engine)"):
                    products = search.run_v1_rerank(
                        client, index, emb, rv.rerank_pipeline, search_query_text=prompt, rerank_query_text=prompt,
                        min_price=lo, max_price=hi, vector_fields=rv.vector_fields, image_model_id=rv.image_model_id)
                answer = "Hybrid search, then reranked by relevance to the query:"
            except OSError as e:
                # Bedrock Rerank is quota-limited (Cohere 3.5: 250 req/min/Region); fall back to plain
                # hybrid rather than fail the request when the rerank call is throttled or times out.
                if not any(t in str(e) for t in ("429", "503", "hrottl", "imed out", "imeout")):
                    raise
                with tl.span("Plain hybrid search (fallback)"):
                    products, fell_back = plain_hybrid(), True
                answer = "Rerank is throttled right now — showing plain hybrid results (automatic fallback):"

        elif kind == "mcp":
            # An agent OUTSIDE the cluster reaching the same search: three MCP calls, its own credentials.
            mcp = get_gateway_client()
            try:
                try:
                    products, mcp_trace = ac.search_products_via_gateway(mcp, prompt, lo, hi)
                except OSError as e:
                    if not ac.is_token_rejection(e):
                        raise
                    get_gateway_client.clear()  # the token was refused (expired or revoked): mint a new one once
                    mcp = get_gateway_client()
                    products, mcp_trace = ac.search_products_via_gateway(mcp, prompt, lo, hi)
                answer = "An outside agent reached the same search over MCP:"
                trace = mcp_trace.steps
                for seg in timing.mcp_segments(trace):
                    tl.add(seg["name"], seg["seconds"])
            except (mcp_mod.McpError, OSError) as e:
                products, answer = [], f"The MCP call failed: {e}"
            notes.append("Reached through an AgentCore Gateway with its own Cognito token (client credentials).")

        else:  # v2 / v3: a Strands agent on AgentCore Runtime, memory in OpenSearch Memory
            mc = get_agentcore_client()
            memory, space_id = get_memory_client(), s.id("space_id")
            agent_kind = "inventory" if use_mcp_agent else "plain"
            if st.session_state.get("agent_memory_agent") != agent_kind:  # the two variants keep separate conversations
                st.session_state.pop("agent_memory_id", None)
            session = st.session_state.get("agent_memory_id") or ac.new_session_id()
            question, no_notes = prompt, True  # no notes: the runtime tells the agent to search, and opens with "no record" when asked about the past
            if kind == "v3":
                if "recalled" not in st.session_state:  # load once per session, before the first turn
                    with tl.span("Recall the shopper's facts (OpenSearch Memory)"):
                        st.session_state["recalled"] = osm.recall(memory, space_id, user_id, prompt)
                recalled = len(st.session_state["recalled"])
                if st.session_state["recalled"]:
                    question, no_notes = osm.facts_preamble(st.session_state["recalled"]) + prompt, False
            t_agent = time.perf_counter()
            reply = ac.invoke_agent(mc, s.id("agent_runtime_arn"), question, session, inventory=use_mcp_agent, no_notes=no_notes)
            tl.add("Agent run (AgentCore Runtime)", time.perf_counter() - t_agent)
            for seg in timing.runtime_segments(reply.timings):
                tl.segments.append(seg)
            st.session_state["agent_memory_id"] = reply.session_id
            st.session_state["agent_memory_agent"] = agent_kind
            answer = reply.answer or "(no answer)"
            steps = reply.steps
            if not steps:
                notes.append("The agent answered without calling a tool.")
            if kind == "v3":
                st.session_state.setdefault("app_session_id", ac.new_session_id())
                with tl.span("Store the conversation (OpenSearch Memory)"):
                    osm.remember_turn(memory, space_id, user_id, st.session_state["app_session_id"],
                                  [{"role": "user", "text": prompt}, {"role": "assistant", "text": answer}])
                notes.append("Conversation stored for this shopper — OpenSearch Memory extracts facts in the background.")

    extras, searched_info = None, None
    if kind in ("v2", "v3"):  # the documents the agent's search returned, which its answer is written from
        found = agent_results.last_search(steps)
        if found:
            products = [search.Product(i, h["product_id"], h["title"], h["price"], h["in_stock"], h["score"])
                        for i, h in enumerate(found["hits"], 1)]
            extras = agent_results.row_extras(found["hits"], answer, saved)
            searched_info = {"query": found["query"], "lo": found["lo"], "hi": found["hi"]}
    if kind in ("keyword", "v0", "v1", "v1+rerank"):
        hood = explain.search_hood(version, prompt, lo, hi, emb, rerank_fell_back=fell_back, retrieval=rv)
    elif kind == "mcp":
        hood = explain.trace_hood(trace)
    else:
        hood = explain.agent_hood(steps)
    moves = ranks.movement(saved, kind, prompt, [p.product_id for p in products])
    return turns_mod.new_turn(
        turns_mod.next_number(saved), version, prompt,
        turns_mod.config_summary(version, lo, hi, inventory=use_mcp_agent, recalled=recalled,
                                 rerank_fell_back=fell_back),
        answer, products, steps, trace, notes, hood=hood, moves=moves, image_for=image_for, extras=extras,
        searched=searched_info, step=guided_step, run=st.session_state.get("guide_run") if guided_step is not None else None,
        timing=tl.finish())


if __name__ == "__main__":
    main()

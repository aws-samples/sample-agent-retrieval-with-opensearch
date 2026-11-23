# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Every control of the Streamlit app, on the Serverless track. No network.

The app is run with fakes for the search, the agents and the memory. What is checked: the controls exist and do what their
label says, saved outputs survive a page refresh (a new browser session) and are cleared only by their own button, the memory
lists can be opened, closed, refreshed and show new events, and nothing in the UI refers to design documents (ADR)."""

import os
from types import SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from retail_agent_demo import agentcore as ac
from retail_agent_demo import config, search
from retail_agent_demo import os_client as osc
from retail_agent_demo import osmemory as osm

APP = os.path.join(os.path.dirname(__file__), "..", "..", "src", "retail_agent_demo", "app.py")
PRODUCT = search.Product(1, "B07QRXG93G", "MASSJOY T-rex bank", 24.99, True, 1.0)
REQUEST = "a dinosaur piggy bank under 30 dollars"


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A factory for fresh browser sessions (AppTest instances) over one shared fake backend."""
    w = SimpleNamespace(serverless=True, facts=[{"memory": "Budget is 30", "strategy_type": "shopper-facts"}],
                        history=[{"action": "ADD", "before": "", "after": "Budget is 30"}], forgot=[], stored=[])
    ids = {"agent_runtime_arn": "arn:runtime", "space_id": "SPACE", "embedding_model_id": "EMB", "products_index": "p",
           "hybrid_pipeline": "h", "hybrid_rerank_pipeline": "r", "catalog_gateway_url": "https://gw",
           "catalog_user_pool_id": "pool", "catalog_client_id": "cid", "catalog_token_endpoint": "https://tok"}  # nosec B105 - test value, not a credential
    settings = SimpleNamespace(ids=ids, id=lambda k: ids[k], name_prefix="sample-agent-ret", user="u", auth="sigv4", region="us-east-1",
                               service="aoss")
    monkeypatch.setenv("APP_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("INVENTORY_MCP_ENABLED", "1")
    monkeypatch.setattr(config, "load_settings", lambda *a, **k: settings)
    monkeypatch.setattr(osc, "OSClient", lambda s, **k: SimpleNamespace(s=s))
    monkeypatch.setattr("boto3.client", lambda *a, **k: SimpleNamespace(
        describe_user_pool_client=lambda **kw: {"UserPoolClient": {"ClientSecret": "s"}}))  # pragma: allowlist secret
    for name in ("run_lexical", "run_v0", "run_v1_inline", "run_v1_rerank"):
        monkeypatch.setattr(search, name, lambda *a, **k: [PRODUCT])
    outside = lambda *x, **k: ([PRODUCT], SimpleNamespace(steps=[{"method": "tools/list", "summary": "tools: x"}]))  # noqa: E731
    monkeypatch.setattr(ac, "search_products_via_gateway", outside)
    monkeypatch.setattr(ac, "cognito_token", lambda *x, **k: "token")
    steps = [{"tool": "run_product_search", "input": "{}", "output": "[]"}]
    monkeypatch.setattr(ac, "invoke_agent", lambda *a, **k: ac.AgentReply(answer="agent reply", session_id="s" * 36, steps=steps))
    monkeypatch.setattr(osm, "recall", lambda *a, **k: ["Budget is 30"])
    monkeypatch.setattr(osm, "remember_turn", lambda *a, **k: w.stored.append(a[2]))
    monkeypatch.setattr(osm, "list_facts", lambda c, m, u: list(w.facts))
    monkeypatch.setattr(osm, "list_activity", lambda c, m, u, **k: [
        {"when": None, "action": "TURN STORED", "detail": d} for d in w.history])
    def wipe(u):  # the forget empties the fake memory of the shopper
        w.forgot.append(u)
        w.facts, w.history = [], []
    monkeypatch.setattr(osm, "forget_shopper_verified", lambda c, m, u, **k: wipe(u) or {"memories": 1, "events": 2, "sessions": 1, "left over": 0})
    w.history = ["user: my budget is 30"]

    def browser_session() -> AppTest:
        st.cache_resource.clear()  # a reload gives a fresh session; the files on disk are what survives
        return AppTest.from_file(APP, default_timeout=30).run()

    w.open = browser_session
    return w


def choose(at, prefix):
    """Select a version by the kind its old label prefix names (v0, v1 , v1+, v2, v3, MCP, Keyword)."""
    from retail_agent_demo.turns import version_kind
    radio = at.sidebar.radio[0]
    radio.set_value(next(o for o in radio.options if version_kind(o) == version_kind(prefix))).run()


def ask(at, text=REQUEST):
    at.text_input(key="query").set_value(text)
    at.button(key="search_go").click().run()
    assert not at.exception, at.exception


def card_lines(at):
    """The rank line of every result card on the page: '**#1** ...'."""
    return [m.value for m in at.markdown if m.value.startswith("**#")]


def button(at, label):
    return next(b for b in at.sidebar.button if b.label.startswith(label))


def sidebar_text(at):
    return "\n".join(m.value for m in at.sidebar.markdown) + "\n".join(c.value for c in at.sidebar.caption)


def test_every_control_is_present_and_the_version_switch_offers_all_seven_versions(world):
    at = world.open()
    from retail_agent_demo.turns import version_kind
    assert at.sidebar.radio[0].options == [
        "Lexical only", "Vector only", "Lexical + vector (hybrid)", "Lexical + vector + rerank",
        "Agent (hybrid search), no memory", "MCP: outside client (hybrid search)", "Agent (hybrid search) + memory"]
    assert [version_kind(o) for o in at.sidebar.radio[0].options] == ["keyword", "v0", "v1", "v1+rerank", "v2", "mcp", "v3"]
    assert at.sidebar.selectbox[0].options == ["bob", "demo-shopper"]
    assert {t.key for t in at.sidebar.toggle} == {"show_facts", "show_history", "guided"}
    labels = {b.label for b in at.sidebar.button}
    assert {"New session", "Clear results"} <= labels and any(label.startswith("⚠️ Forget this shopper") for label in labels)
    assert not at.sidebar.checkbox  # the inventory switch is for the agent versions only
    choose(at, "v2")
    assert [c.label for c in at.sidebar.checkbox] == ["Agent also calls the inventory MCP server"]


def test_nothing_in_the_ui_refers_to_a_design_document(world):
    at = world.open()
    choose(at, "v2")
    everything = sidebar_text(at) + "".join(str(c.proto) for c in list(at.sidebar.checkbox) + list(at.sidebar.toggle))
    everything += "".join(m.value for m in at.markdown) + "".join(c.value for c in at.caption)
    assert "ADR" not in everything


def test_outputs_survive_a_page_refresh(world):
    at = world.open()
    choose(at, "v1 ")
    ask(at)
    choose(at, "v2")
    ask(at, "another request")
    assert len(at.expander) == 2
    refreshed = world.open()  # a brand new browser session, as after a reload
    assert len(refreshed.expander) == 2
    assert "Agent (hybrid search), no memory" in refreshed.expander[0].label and "Lexical + vector (hybrid)" in refreshed.expander[1].label
    assert [f.proto.expanded for f in refreshed.expander] == [True, False]  # newest first, and open
    assert card_lines(refreshed)  # the result cards are still there, not just the text


def test_clear_results_clears_the_page_and_the_saved_copy_so_a_refresh_stays_empty(world):
    at = world.open()
    choose(at, "v1 ")
    ask(at)
    button(at, "Clear results").click().run()
    assert len(at.expander) == 0
    assert len(world.open().expander) == 0
    assert button(world.open(), "Clear results").proto.disabled  # nothing to clear


def test_new_session_keeps_the_outputs(world):
    at = world.open()
    choose(at, "v2")
    ask(at)
    button(at, "New session").click().run()
    assert len(at.expander) == 1
    assert len(world.open().expander) == 1


def test_each_shopper_has_their_own_saved_outputs(world):
    at = world.open()
    choose(at, "v1 ")
    ask(at)
    at.sidebar.selectbox[0].set_value("demo-shopper").run()
    assert len(at.expander) == 0
    ask(at, "demo shopper request")
    assert len(at.expander) == 1
    at.sidebar.selectbox[0].set_value("bob").run()
    assert len(at.expander) == 1 and "dinosaur" in at.expander[0].label  # bob's own output is back


def test_the_facts_list_opens_closes_and_reopens_any_number_of_times(world):
    at = world.open()
    for _ in range(3):
        at.sidebar.toggle(key="show_facts").set_value(True).run()
        assert "Budget is 30" in sidebar_text(at)
        at.sidebar.toggle(key="show_facts").set_value(False).run()
        assert "Budget is 30" not in sidebar_text(at)


def test_an_open_list_shows_new_events_after_refresh_now(world):
    at = world.open()
    at.sidebar.toggle(key="show_facts").set_value(True).run()
    at.sidebar.toggle(key="show_history").set_value(True).run()
    world.facts.insert(0, {"memory": "Budget is 40", "strategy_type": "shopper-facts"})
    world.history.insert(0, "user: my budget is 40")
    assert "Budget is 40" not in sidebar_text(at)  # not read yet
    button(at, "Refresh now").click().run()
    text = sidebar_text(at)
    assert "Budget is 40" in text and "my budget is 40" in text
    button(at, "Refresh now").click().run()  # and again: the button keeps working
    assert "Budget is 40" in sidebar_text(at)


def test_the_history_list_opens_and_closes_repeatedly(world):
    at = world.open()
    for _ in range(3):
        at.sidebar.toggle(key="show_history").set_value(True).run()
        text = sidebar_text(at)
        assert "TURN STORED" in text
        at.sidebar.toggle(key="show_history").set_value(False).run()
        assert "Memory history" not in sidebar_text(at).replace("Show memory history", "")


def test_forgetting_a_shopper_clears_memory_and_the_saved_outputs_and_reports_the_counts(world):
    at = world.open()
    choose(at, "v1 ")
    ask(at)
    button(at, "⚠️ Forget this shopper").click().run()
    assert world.forgot == ["bob"]
    assert len(at.expander) == 0 and len(world.open().expander) == 0
    assert any("memories" in j.value for j in at.sidebar.json)


def test_a_memory_read_that_fails_does_not_break_the_page(world, monkeypatch):
    monkeypatch.setattr(osm, "list_facts", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("throttled")))
    at = world.open()
    at.sidebar.toggle(key="show_facts").set_value(True).run()
    assert not at.exception
    assert "Could not read memory" in sidebar_text(at)


def test_v3_saves_its_turn_to_memory_and_the_fold_names_the_recalled_facts(world):
    at = world.open()
    choose(at, "v3")
    ask(at, "which bank was I looking at?")
    assert world.stored == ["bob"]
    assert "1 recalled fact" in at.expander[0].label


# ---- the search bar, the keyword version, the panel under the hood, rank movement and the guided demo ----------------
def hood_text(at):
    return "\n".join(m.value for m in at.markdown)


def test_the_keyword_version_runs_a_lexical_search_and_says_so(world, monkeypatch):
    seen = []
    monkeypatch.setattr(search, "run_lexical", lambda client, index, q, lo, hi, **k: seen.append((q, lo, hi)) or [PRODUCT])
    at = world.open()
    assert at.radio(key="version").value == "Lexical only"  # the ladder starts here
    ask(at)
    assert seen == [(REQUEST, 0, 30)]
    assert "keyword (BM25)" in at.expander[0].label and card_lines(at)


def test_the_search_bar_is_a_form_the_results_are_cards_and_a_search_needs_text(world):
    at = world.open()
    assert at.text_input(key="query").placeholder == "Search products…"
    choose(at, "v2")
    assert at.text_input(key="query").placeholder == "Ask the shopping assistant…"
    at.button(key="search_go").click().run()  # nothing typed: nothing runs
    assert len(at.expander) == 0
    ask(at)
    assert len(at.expander) == 1  # the agent versions show the answer and its thinking


def test_every_search_version_shows_the_request_it_sent_under_the_hood(world):
    at = world.open()
    for prefix, expect in (("Keyword", "Keyword (lexical) query"), ("v0", "Vector query"), ("v1 ", "Fusion"),
                           ("v1+", "Rerank")):
        choose(at, prefix)
        ask(at)
        assert "Under the hood" in hood_text(at) and expect in hood_text(at), prefix
    assert any("multi_match" in str(j.value) for j in at.json)  # the request body is there to open


def test_the_agent_versions_show_their_thinking_not_the_search_request(world):
    at = world.open()
    choose(at, "v2")
    ask(at)
    assert "The agent runs the search" in hood_text(at) and "Fusion" not in hood_text(at)


def test_rank_movement_is_shown_against_the_previous_rung_and_in_the_ladder(world, monkeypatch):
    a, b = (search.Product(1, "A", "Alpha bank", 10.0, True, 1.0), search.Product(2, "B", "Beta bank", 11.0, True, 0.9))
    monkeypatch.setattr(search, "run_lexical", lambda *x, **k: [a, b])
    monkeypatch.setattr(search, "run_v0", lambda *x, **k: [search.Product(1, "B", "Beta bank", 11.0, True, 1.0),
                                                           search.Product(2, "A", "Alpha bank", 10.0, True, 0.9)])
    at = world.open()
    choose(at, "Keyword")
    ask(at)
    assert not any("▲" in m or "▼" in m for m in card_lines(at))  # the first rung has nothing to compare with
    choose(at, "v0")
    ask(at)
    moved = card_lines(at)
    assert any("▲1" in m for m in moved) and any("▼1" in m for m in moved)
    assert any("Rank ladder" in m.value for m in at.markdown)
    ladder = next(m.value for m in at.markdown if m.value.startswith("<table") and "Vector" in m.value and "share of the total" not in m.value)
    assert "Lexical" in ladder and "Vector" in ladder and "#2" in ladder


def test_the_guided_demo_walks_the_steps_and_each_next_selects_the_version_and_runs_the_request(world):
    from retail_agent_demo import acts
    at = world.open()
    at.sidebar.toggle(key="guided").set_value(True).run()
    assert "Guided demo" in hood_text(at) and len(at.expander) == 0
    at.button(key="guide_start").click().run()
    first = acts.first()
    assert first == 2  # Start opens at step 3; steps 1 and 2 stay in the list and behind Back
    assert at.radio(key="step_menu").value == acts.menu()[first] and at.text_input(key="query").value == acts.STEPS[first].query
    assert len(at.expander) == 1  # the first shown step ran by itself
    assert "Act 1 of 4" in hood_text(at) and "step 3 of 5" in hood_text(at) and "overall 3 of 11" in hood_text(at)
    assert not at.button(key="guide_back").proto.disabled  # steps 1 and 2 are still behind Back
    at.button(key="guide_next").click().run()
    assert at.text_input(key="query").value == acts.REWRITE and len(at.expander) == 2
    assert at.radio(key="step_menu").value == acts.menu()[first + 1]
    at.button(key="guide_back").click().run()  # Back goes to the previous step and runs it, as Next does
    assert at.radio(key="step_menu").value == acts.menu()[first] and at.text_input(key="query").value == acts.REWRITE
    assert len(at.expander) == 3 and "step 3 of 5" in hood_text(at)
    at.button(key="guide_back").click().run()  # and Back goes on to step 2 (vector), then step 1
    assert at.radio(key="step_menu").value == acts.menu()[1] and "step 2 of 5" in hood_text(at)


def test_the_guided_demo_reaches_the_last_step_with_bob_and_the_lists_open(world):
    from retail_agent_demo import acts
    at = world.open()
    at.sidebar.toggle(key="guided").set_value(True).run()
    at.button(key="guide_start").click().run()
    for _ in range(len(acts.STEPS) - 1 - acts.first()):
        at.button(key="guide_next").click().run()
        assert not at.exception, at.exception
    assert at.sidebar.selectbox[0].value == "bob"  # every step is Bob's: the demo never switches shopper
    assert at.sidebar.toggle(key="show_facts").value and at.sidebar.toggle(key="show_history").value
    assert at.button(key="guide_next").proto.disabled
    assert "Act 4 of 4" in hood_text(at)


def test_the_inventory_step_turns_the_switch_on(world):
    from retail_agent_demo import acts
    at = world.open()
    at.sidebar.toggle(key="guided").set_value(True).run()
    at.button(key="guide_start").click().run()
    inventory = next(i for i, s in enumerate(acts.STEPS) if s.inventory)
    for _ in range(inventory - acts.first()):
        at.button(key="guide_next").click().run()
    assert at.radio(key="step_menu").value == acts.menu()[7] and at.sidebar.checkbox(key="use_inventory_mcp").value is True


def test_the_start_parameter_opens_the_guided_demo_at_that_step_and_runs_it(world):
    from retail_agent_demo import acts
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["start"] = "2"
    at.run()
    assert not at.exception, at.exception
    assert at.sidebar.toggle(key="guided").value and at.radio(key="step_menu").value == acts.menu()[2]
    assert at.text_input(key="query").value == acts.STEPS[2].query and len(at.expander) == 1
    assert "step 3 of 5" in hood_text(at)


def test_dollar_signs_in_the_guided_banner_are_escaped_so_they_do_not_turn_into_math(world):
    from retail_agent_demo import acts
    at = world.open()
    at.sidebar.toggle(key="guided").set_value(True).run()
    at.button(key="guide_start").click().run()
    budget = next(i for i, s in enumerate(acts.STEPS) if "$" in s.says)
    for _ in range(budget - acts.first()):
        at.button(key="guide_next").click().run()
    assert any("\\$30" in m.value and "\\$40" in m.value for m in at.markdown)


def test_a_saved_result_without_a_picture_path_still_shows_the_picture_found_by_product_id(world, tmp_path, monkeypatch):
    from retail_agent_demo import app as app_mod
    from retail_agent_demo import images
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"B07QRXG93G": "x.jpg"}')
    (tmp_path / "x.jpg").write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(images, "DIR", tmp_path)
    images.manifest.cache_clear()
    assert images.image_for("B07QRXG93G") == str(tmp_path / "x.jpg") and images.image_for("nope") is None
    assert app_mod.image_for is images.image_for
    images.manifest.cache_clear()


def test_an_agent_fold_shows_cards_and_a_table_of_the_documents_it_retrieved_and_the_query_it_sent(world, monkeypatch):
    hits = [{"product_id": "B07QRXG93G", "title": "MASSJOY Resin Large Dinosaur Piggy Bank", "price": 24.99, "in_stock": True, "score": 1.0},
            {"product_id": "B2", "title": "Cartoon Piggy Bank Panda", "price": 13.87, "in_stock": False, "score": 0.5}]
    steps = [{"tool": "run_product_search", "input": "{}", "output": "[]", "hits": hits,
              "raw_input": '{"query": "dinosaur piggy bank", "min_price": 0, "max_price": 30}'}]
    monkeypatch.setattr(ac, "invoke_agent", lambda *a, **k: ac.AgentReply(answer="Try the MASSJOY Dinosaur Piggy Bank at $24.99.",
                                                                          session_id="s" * 36, steps=steps))
    at = world.open()
    choose(at, "v1 ")
    ask(at, "dinosaur piggy bank money bank kids under 30 dollars")  # an earlier result the table can refer to
    choose(at, "v2")
    ask(at, "a gift for a six year old who loves dinosaurs")
    text = "\n".join(m.value for m in at.markdown)
    assert "The agent searched for:" in text and "dinosaur piggy bank" in text and "price $0 to $30" in text
    assert "Documents the agent retrieved" in text
    table = next(m.value for m in at.markdown if m.value.startswith("<table") and "in the answer" in m.value)
    assert "MASSJOY" in table and "✓ yes" in table and "out of stock" in table
    assert "Lexical + vector #1" in table  # where MASSJOY sat in the earlier hybrid result
    assert "background:#d7f5d7" in table  # the product the demo follows: bold on light green
    assert any("in the answer" in m for m in card_lines(at))


def test_the_search_text_and_the_query_lines_are_larger_and_bold(world):
    at = world.open()
    ask(at)
    assert any("font-size:1.25rem" in m.value and "font-weight:700" in m.value for m in at.markdown)
    assert any("font-size:1.2rem" in m.value and "<b>" in m.value for m in at.markdown)


def test_a_pipe_in_a_product_title_does_not_split_the_table_row(world, monkeypatch):
    hits = [{"product_id": "B1", "title": "Piggy Bank for Adults | Password Piggy Bank", "price": 17.28, "in_stock": True, "score": 1.0}]
    steps = [{"tool": "run_product_search", "input": "{}", "output": "[]", "hits": hits,
              "raw_input": '{"query": "bank", "min_price": 0, "max_price": 30}'}]
    monkeypatch.setattr(ac, "invoke_agent", lambda *a, **k: ac.AgentReply(answer="ok", session_id="s" * 36, steps=steps))
    at = world.open()
    choose(at, "v2")
    ask(at, "a bank")
    table = next(m.value for m in at.markdown if m.value.startswith("<table") and "in the answer" in m.value)
    assert "Piggy Bank for Adults | Password Piggy Bank" in table  # an HTML cell: the pipe is just text
    body = table.split("<tbody>")[1]
    assert body.count("<td") == 6  # one row, six cells


def test_the_search_bar_is_framed_in_a_colour_and_the_input_stays_white(world):
    at = world.open()
    css = "\n".join(m.value for m in at.markdown if "<style>" in m.value)
    assert "stForm" in css and "#0972d3" in css and "#e3f0ff" in css and "background:#ffffff" in css


def test_the_guided_demo_keeps_a_running_comparison_that_grows_with_each_step(world):
    from retail_agent_demo import acts
    at = world.open()
    at.sidebar.toggle(key="guided").set_value(True).run()
    at.button(key="guide_start").click().run()
    assert at.session_state["guide_run"]  # every guided run has its own id, so an old run never leaks into the table
    a, b = acts.first(), acts.first() + 1
    first = [m.value for m in at.markdown if m.value.startswith("<table") and acts.short(a) in m.value]
    assert first and acts.short(b) not in first[0]
    assert f"{acts.short(0)}</th>" not in first[0] and f"{acts.short(1)}</th>" not in first[0]  # steps 1 and 2 were not run: no column
    assert "Comparison so far" in hood_text(at)
    at.button(key="guide_next").click().run()
    second = next(m.value for m in at.markdown if m.value.startswith("<table") and acts.short(b) in m.value)
    assert acts.short(a) in second and acts.short(b) in second  # the second step builds on the first
    assert "background:#d7f5d7" in second  # the T-rex bank row
    at.button(key="guide_back").click().run()  # Back runs the first shown step again: still two columns, the first one refreshed
    third = next(m.value for m in at.markdown if m.value.startswith("<table") and acts.short(b) in m.value)
    assert third.count("<th ") == 3


def test_the_guided_sidebar_lists_all_eleven_steps_in_order_and_the_selection_walks_down_it(world):
    from retail_agent_demo import acts
    at = world.open()
    at.sidebar.toggle(key="guided").set_value(True).run()
    assert list(at.radio(key="step_menu").options) == acts.menu() and len(acts.menu()) == len(acts.STEPS) == 11
    assert acts.menu()[0].startswith("1. ")  # steps 1 and 2 stay in the list; Start opens at 3
    at.button(key="guide_start").click().run()
    for i in range(3, 7):
        at.button(key="guide_next").click().run()
        assert at.radio(key="step_menu").value == acts.menu()[i]


def test_picking_a_step_in_the_list_jumps_to_it_and_runs_it(world):
    from retail_agent_demo import acts
    at = world.open()
    at.sidebar.toggle(key="guided").set_value(True).run()
    at.button(key="guide_start").click().run()
    at.radio(key="step_menu").set_value(acts.menu()[3]).run()
    assert at.text_input(key="query").value == acts.STEPS[3].query and len(at.expander) == 2
    assert "step 4 of 5" in hood_text(at)


def test_leaving_the_guided_demo_brings_back_the_plain_version_list_on_the_version_it_ended_on(world):
    at = world.open()
    at.sidebar.toggle(key="guided").set_value(True).run()
    at.button(key="guide_start").click().run()
    at.button(key="guide_next").click().run()
    at.sidebar.toggle(key="guided").set_value(False).run()
    assert at.radio(key="version").value == "Lexical + vector + rerank" and len(at.radio(key="version").options) == 7


def test_an_agent_step_adds_its_column_to_the_comparison_with_the_documents_it_retrieved(world):
    from retail_agent_demo import acts
    at = world.open()
    at.sidebar.toggle(key="guided").set_value(True).run()
    at.button(key="guide_start").click().run()
    for _ in range(5):
        at.button(key="guide_next").click().run()
    table = next(m.value for m in at.markdown if m.value.startswith("<table") and acts.short(5) in m.value)
    assert acts.short(4) in table and acts.short(5) in table


def acts_menu_agent():
    from retail_agent_demo import acts
    return acts.menu()[5]  # "Agent (hybrid search), no memory"


def test_every_fold_states_how_long_the_request_took_and_where_the_time_went(world):
    at = world.open()
    choose(at, "v1")
    ask(at)
    assert " · took " in at.expander[0].label
    table = next(m.value for m in at.markdown if m.value.startswith("<table") and "share of the total" in m.value)
    assert "Hybrid search" in table
    assert any(m.value.startswith("**Took ") for m in at.markdown)


def test_an_agent_fold_lists_the_steps_of_its_run(world, monkeypatch):
    reply = ac.AgentReply(answer="r", session_id="s" * 36, steps=[], timings=[{"name": "Model turn", "seconds": 1.5},
                                                                           {"name": "run_product_search", "seconds": 0.4}])
    monkeypatch.setattr(ac, "invoke_agent", lambda *a, **k: reply)
    at = world.open()
    choose(at, "v2")
    ask(at)
    table = next(m.value for m in at.markdown if m.value.startswith("<table") and "share of the total" in m.value)
    assert "Agent run" in table and "Model turn" in table and "↳" in table


def test_the_t_rex_card_gets_the_colour_of_its_grid_row(world, monkeypatch):
    """The demo's T-rex bank is shaded in the comparison grid; its result card carries the same colour (a -target key and a CSS rule)."""
    from unittest.mock import MagicMock

    from retail_agent_demo import acts, ui_tables
    from retail_agent_demo import app as app_mod
    css = "\n".join(m.value for m in world.open().markdown if "<style>" in m.value)
    assert f"[class*='st-key-card-'][class*='-target']{{background:{ui_tables.HIGHLIGHT};}}" in css
    fake = MagicMock()
    fake.columns.side_effect = lambda n: [MagicMock() for _ in range(n)]
    monkeypatch.setattr(app_mod, "st", fake)
    monkeypatch.setattr(app_mod, "image_for", lambda pid: None)
    row = {"#": 1, "id": acts.TARGET, "product": "T-rex bank", "price": "$24.99", "in stock": "✅"}
    other = {"#": 2, "id": "B0OTHER", "product": "Pig", "price": "$9.99", "in stock": "✅"}
    app_mod.render_cards([row, other], "turn1")
    keys = [c.kwargs["key"] for c in fake.container.call_args_list]
    assert keys == ["card-turn1-1-target", "card-turn1-2"]

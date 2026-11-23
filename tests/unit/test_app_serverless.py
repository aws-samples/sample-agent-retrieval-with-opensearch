# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The Streamlit app on the Serverless track (BACKEND=serverless): AgentCore for the agent and MCP, OpenSearch Memory for memory. No network."""

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
IDS = {"agent_runtime_arn": "arn:runtime", "space_id": "SPACE", "embedding_model_id": "EMB", "products_index": "p",
       "hybrid_rerank_pipeline": "pipe", "catalog_gateway_url": "https://gw", "catalog_user_pool_id": "pool",
       "catalog_client_id": "cid", "catalog_token_endpoint": "https://tok"}  # nosec B105 - test value, not a credential
STEPS = [{"tool": "run_product_search", "input": '{"query": "dinosaur bank"}', "output": "[]"},
         {"tool": "check_stock", "input": "{}", "output": "Reno, NV", "external": "inventory MCP server"}]


@pytest.fixture
def calls(monkeypatch, tmp_path):
    rec = {"invoke": [], "store": [], "recall": [], "v1": []}
    monkeypatch.setenv("APP_STATE_DIR", str(tmp_path))  # the app keeps its saved outputs here

    def make():
        settings = SimpleNamespace(ids=IDS, id=lambda k: IDS[k], name_prefix="sample-agent-ret", user=None, auth="sigv4",
                                   service="aoss", region="us-east-1")
        monkeypatch.setattr(config, "load_settings", lambda *a, **k: settings)
        monkeypatch.setattr(osc, "OSClient", lambda s, **k: SimpleNamespace(s=s))
        monkeypatch.setattr("boto3.client", lambda *a, **k: SimpleNamespace())

        def invoke(client, arn, prompt, session, inventory=False, no_notes=False):
            rec["invoke"].append((prompt, session, inventory))
            rec.setdefault("no_notes", []).append(no_notes)
            return ac.AgentReply(answer="Try the T-rex bank.", session_id=session, steps=STEPS)

        monkeypatch.setattr(ac, "invoke_agent", invoke)
        monkeypatch.setattr(osm, "recall", lambda c, m, u, q, k=5: rec["recall"].append((u, q)) or ["Budget is 30 dollars"])
        monkeypatch.setattr(osm, "remember_turn", lambda c, m, u, s, msgs: rec["store"].append((u, s, [x["role"] for x in msgs])))
        monkeypatch.setattr(osm, "list_facts", lambda c, m, u: [{"memory": "Budget is 40", "strategy_type": "shopper-facts"}])
        monkeypatch.setattr(osm, "list_activity", lambda c, m, u, **k: [
            {"when": None, "action": "TURN STORED", "detail": "user: my budget is 40"}])
        monkeypatch.setattr(osm, "forget_shopper_verified",
                            lambda c, m, u, **k: {"memories": 2, "events": 3, "sessions": 1, "left over": 0})
        monkeypatch.setattr(search, "run_v0", lambda *a, **k: [search.Product(1, "B1", "T-rex bank", 24.99, True, 1.0)])
        monkeypatch.setattr(search, "run_v1_inline", lambda *a, **k: rec["v1"].append(a[3]) or [
            search.Product(1, "B1", "T-rex bank", 24.99, True, 1.0)])
        st.cache_resource.clear()
        return AppTest.from_file(APP, default_timeout=30).run()

    make.rec = rec
    return make


def choose(at, prefix):
    """Select a version by the kind its old label prefix names (v0, v1 , v1+, v2, v3, MCP, Keyword)."""
    from retail_agent_demo.turns import version_kind
    radio = at.sidebar.radio[0]
    radio.set_value(next(o for o in radio.options if version_kind(o) == version_kind(prefix))).run()


def ask(at, text="a dinosaur piggy bank under 30 dollars"):
    at.text_input(key="query").set_value(text)
    at.button(key="search_go").click().run()
    assert not at.exception, at.exception


def test_v1_sends_the_hybrid_query_inline_because_serverless_has_no_stored_template(calls):
    at = calls()
    choose(at, "v1 ")
    ask(at)
    assert calls.rec["v1"] == ["a dinosaur piggy bank under 30 dollars"]


def test_v2_runs_the_hosted_agent_and_lists_its_steps(calls):
    at = calls()
    choose(at, "v2")
    ask(at)
    prompt, session, inventory = calls.rec["invoke"][0]
    assert prompt == "a dinosaur piggy bank under 30 dollars" and len(session) >= 33 and inventory is False
    assert calls.rec["no_notes"] == [True]  # nothing remembered: the runtime adds the search hint and the "no record" opening
    assert any("Under the hood" in m.value for m in at.markdown)
    shown = "\n".join(m.value for m in at.markdown)
    assert "The agent runs the search" in shown and "Asks the inventory server: check_stock" in shown
    assert not calls.rec["store"]  # v2 has no memory


def test_v2_keeps_the_same_session_across_turns(calls):
    at = calls()
    choose(at, "v2")
    ask(at)
    ask(at, "and a green one?")
    assert calls.rec["invoke"][0][1] == calls.rec["invoke"][1][1]


def test_v3_recalls_before_the_first_turn_passes_the_facts_and_stores_each_turn_for_the_shopper(calls):
    at = calls()
    choose(at, "v3")
    ask(at, "Which bank was I looking at?")
    ask(at, "My budget is 40 dollars now.")
    assert calls.rec["recall"] == [("bob", "Which bank was I looking at?")]  # once per session
    assert "shopper-notes" in calls.rec["invoke"][0][0] and "Budget is 30 dollars" in calls.rec["invoke"][0][0]
    assert [u for u, _, _ in calls.rec["store"]] == ["bob", "bob"]
    assert calls.rec["store"][0][2] == ["user", "assistant"]


def test_the_memory_panel_lists_records_and_a_history_timeline(calls):
    at = calls()
    at.sidebar.toggle(key="show_facts").set_value(True).run()
    assert "Budget is 40" in "\n".join(m.value for m in at.sidebar.markdown)
    at.sidebar.toggle(key="show_history").set_value(True).run()
    shown = "\n".join(m.value for m in at.sidebar.markdown)
    assert "TURN STORED" in shown and "my budget is 40" in shown


def test_forgetting_a_shopper_calls_agentcore(calls):
    at = calls()
    next(b for b in at.sidebar.button if "Forget this shopper" in b.label).click().run()
    assert not at.exception
    assert any("events" in j.value for j in at.json)


def test_an_expired_gateway_token_is_replaced_once_and_the_search_retried(calls, monkeypatch):
    at = calls()
    attempts = []

    def search_via_gateway(client, query, lo, hi, size=5):
        attempts.append(client.token)
        if len(attempts) == 1:
            raise OSError('POST gateway -> 403: {"message":"insufficient_scope - higher privileges"}')
        return [search.Product(1, "B1", "T-rex bank", 24.99, True, 1.0)], SimpleNamespace(steps=[])

    minted = iter(["old-token", "new-token"])
    monkeypatch.setattr(ac, "search_products_via_gateway", search_via_gateway)
    monkeypatch.setattr(ac, "cognito_token", lambda *a, **k: next(minted))
    monkeypatch.setattr("boto3.client", lambda *a, **k: SimpleNamespace(
        describe_user_pool_client=lambda **kw: {"UserPoolClient": {"ClientSecret": "s"}}))  # pragma: allowlist secret
    st.cache_resource.clear()
    choose(at, "MCP")
    ask(at, "dinosaur piggy bank money bank kids under 30 dollars")
    assert attempts == ["old-token", "new-token"]
    assert not at.exception


def fold_labels(at):
    return [e.label for e in at.expander]


def test_every_output_is_kept_in_a_fold_named_with_its_configuration(calls):
    at = calls()
    choose(at, "v0")
    ask(at, "dinosaur bank under 30 dollars")
    choose(at, "v1 ")
    ask(at, "dinosaur bank under 30 dollars")
    choose(at, "v2")
    ask(at, "a dinosaur piggy bank")
    labels = fold_labels(at)
    assert len(labels) == 3  # the earlier outputs are still on the page after later turns
    assert "**3 · Agent (hybrid search), no memory" in labels[0] and "Strands agent on AgentCore Runtime" in labels[0]  # newest first
    assert "**2 · Lexical + vector (hybrid)" in labels[1] and "0.3 keyword + 0.65 text vector + 0.05 picture vector" in labels[1] and "inside the query" in labels[1]
    assert "**1 · Vector only" in labels[2] and "vector only" in labels[2] and "OpenSearch Serverless" in labels[2]
    assert len([m for m in at.markdown if m.value.startswith("**#")]) == 2  # the cards of turns 1 and 2 are still there


def test_the_latest_fold_is_open_and_the_rest_are_closed(calls):
    at = calls()
    choose(at, "v1 ")
    ask(at, "dinosaur bank")
    ask(at, "red backpack")
    folds = at.expander
    assert len(folds) == 2
    assert [f.proto.expanded for f in folds] == [True, False]


def test_new_session_keeps_the_saved_outputs_but_switching_shopper_clears_them(calls):
    at = calls()
    choose(at, "v1 ")
    ask(at, "dinosaur bank")
    next(b for b in at.sidebar.button if b.label == "New session").click().run()
    assert len(at.expander) == 1
    at.sidebar.selectbox[0].set_value("demo-shopper").run()
    assert len(at.expander) == 0  # another shopper has his own saved outputs (none yet)


def test_clear_results_empties_the_page(calls):
    at = calls()
    choose(at, "v1 ")
    ask(at, "dinosaur bank")
    next(b for b in at.sidebar.button if b.label == "Clear results").click().run()
    assert len(at.expander) == 0

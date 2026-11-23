# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""OpenSearch Memory helpers for the Serverless track. No network: the boto3 client is a stub."""

from datetime import datetime, timezone

import pytest
from botocore.exceptions import ClientError

from retail_agent_demo import osmemory as osm


def t(h, m, s=0):
    return datetime(2026, 11, 17, h, m, s, tzinfo=timezone.utc)


class FakeSpace:
    """What the service returns for one actor: memories from search_memories, events from search_events."""

    def __init__(self):
        self.calls, self.created, self.deleted = [], [], []
        self.memories = [
            {"memoryId": "mem-1", "memoryType": "SEMANTIC", "strategyName": "shopper-facts", "content": "The user's budget is 40 dollars.",
             "score": 1.0, "systemMetadata": {"actorId": "bob", "sessionId": "s1", "createdAt": t(10, 0), "updatedAt": t(10, 5)}},
            {"memoryId": "mem-2", "memoryType": "SEMANTIC", "strategyName": "shopper-facts", "content": "Sam turns six.",
             "score": 1.0, "systemMetadata": {"actorId": "bob", "sessionId": "s1", "createdAt": t(9, 0), "updatedAt": t(9, 0, 1)}}]
        self.events = [
            {"eventId": "ev-1", "score": 1.0, "matchedContent": "USER: my budget is 40\nASSISTANT: noted",
             "systemMetadata": {"actorId": "bob", "sessionId": "s1", "createdAt": t(9, 30), "updatedAt": t(9, 30)}}]

    def create_event(self, **kw):
        self.created.append(kw)

    def search_memories(self, **kw):
        self.calls.append(("search_memories", kw))
        return {"memories": list(self.memories)}

    def search_events(self, **kw):
        self.calls.append(("search_events", kw))
        return {"events": list(self.events)}

    def delete_memory(self, **kw):
        self.deleted.append(kw["memoryId"])
        self.memories = [m for m in self.memories if m["memoryId"] != kw["memoryId"]]

    def delete_event(self, **kw):
        self.deleted.append(kw["eventId"])
        self.events = [e for e in self.events if e["eventId"] != kw["eventId"]]


@pytest.mark.parametrize("bad", ["", "bob/../alice", "a/b"])
def test_an_actor_that_could_reach_another_shopper_is_rejected(bad):
    with pytest.raises(ValueError):
        osm.check_actor(bad)
    with pytest.raises(ValueError):
        osm.recall(FakeSpace(), "sp-1", bad, "q")


def test_store_turn_writes_one_conversational_event_for_the_actor_with_roles_uppercased():
    m = FakeSpace()
    osm.store_turn(m, "sp-1", "bob", "sess", [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}])
    e = m.created[0]
    assert (e["spaceId"], e["actorId"], e["sessionId"]) == ("sp-1", "bob", "sess")
    msgs = e["payload"]["conversational"]["unified"]["source"]["object"]["messages"]
    assert msgs == [{"role": "USER", "content": {"text": "hi"}}, {"role": "ASSISTANT", "content": {"text": "hello"}}]


def test_recall_is_a_hybrid_search_for_this_actor_only_and_returns_the_top_k_texts():
    m = FakeSpace()
    assert osm.recall(m, "sp-1", "bob", "what is my budget", k=1) == ["The user's budget is 40 dollars."]
    kind, kw = m.calls[0]
    assert kind == "search_memories" and kw["actorId"] == "bob" and kw["spaceId"] == "sp-1" and kw["maxResults"] == 1
    queries = kw["query"]["hybrid"]["queries"]
    assert queries[0] == {"match": {"memory_text": "what is my budget"}}
    assert queries[1] == {"neural": {"memory_vector": {"query_text": "what is my budget", "k": 1}}}


def test_a_long_question_is_cut_to_what_the_service_accepts():
    q = osm.recall_query("x" * 5000, 500)["hybrid"]["queries"]
    assert len(q[0]["match"]["memory_text"]) == 2048 and q[1]["neural"]["memory_vector"]["k"] == 100


def test_list_facts_is_newest_first_and_names_the_strategy():
    facts = osm.list_facts(FakeSpace(), "sp-1", "bob")
    assert [f["memory"] for f in facts] == ["The user's budget is 40 dollars.", "Sam turns six."]
    assert [f["strategy_type"] for f in facts] == ["shopper-facts", "shopper-facts"]
    assert facts[0]["id"] == "mem-1"


def test_search_pages_through_every_result_for_the_actor():
    pages = iter([{"memories": [{"memoryId": "a", "content": "x"}], "nextToken": "t1"}, {"memories": [{"memoryId": "b", "content": "y"}]}])
    seen = []

    class Paged:
        def search_memories(self, **kw):
            seen.append(kw.get("nextToken"))
            return next(pages)

    assert {f["id"] for f in osm.list_facts(Paged(), "sp-1", "bob")} == {"a", "b"}
    assert seen == [None, "t1"]


def test_forget_deletes_memories_and_events_and_counts_them():
    m = FakeSpace()
    assert osm.forget_shopper(m, "sp-1", "bob") == {"memories": 2, "events": 1, "sessions": 1}
    assert m.deleted == ["mem-1", "mem-2", "ev-1"]


def test_forgetting_something_already_gone_is_not_an_error():
    class Raced(FakeSpace):
        def delete_memory(self, **kw):
            raise ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "gone"}}, "DeleteMemory")

    assert osm.forget_shopper(Raced(), "sp-1", "bob")["memories"] == 2


def test_forget_verified_keeps_deleting_until_a_search_finds_nothing_left():
    class Lagging(FakeSpace):
        """A deleted event still shows in the first search after the delete."""

        def __init__(self):
            super().__init__()
            self.lag = 1

        def search_events(self, **kw):
            if not self.events and self.lag:
                self.lag -= 1
                return {"events": [{"eventId": "ev-1", "matchedContent": "x", "systemMetadata": {"sessionId": "s1"}}]}
            return super().search_events(**kw)

    out = osm.forget_shopper_verified(Lagging(), "sp-1", "bob", sleep=lambda s: None)
    assert out["left over"] == 0 and out["memories"] == 2 and out["events"] >= 1


def test_forget_verified_reports_what_is_left_when_it_cannot_finish():
    class Stuck(FakeSpace):
        def delete_memory(self, **kw):
            pass

    out = osm.forget_shopper_verified(Stuck(), "sp-1", "bob", attempts=2, sleep=lambda s: None)
    assert out["left over"] == 2


def test_activity_is_a_newest_first_timeline_of_turns_and_memory_changes():
    rows = osm.list_activity(FakeSpace(), "sp-1", "bob")
    assert [r["action"] for r in rows] == ["MEMORY UPDATED", "MEMORY CREATED", "TURN STORED", "MEMORY CREATED"]
    assert rows[0]["detail"] == "The user's budget is 40 dollars."
    assert next(r for r in rows if r["action"] == "TURN STORED")["detail"] == "USER: my budget is 40 · ASSISTANT: noted"
    whens = [r["when"] for r in rows]
    assert whens == sorted(whens, reverse=True)


def test_a_memory_touched_only_at_creation_is_not_reported_as_updated():
    rows = [r for r in osm.list_activity(FakeSpace(), "sp-1", "bob") if r["detail"] == "Sam turns six."]
    assert [r["action"] for r in rows] == ["MEMORY CREATED"]  # updated one second later is the same write


def test_activity_for_a_shopper_with_nothing_stored_is_empty():
    m = FakeSpace()
    m.memories, m.events = [], []
    assert osm.list_activity(m, "sp-1", "nobody") == []


def test_wait_for_records_returns_as_soon_as_the_minimum_is_reached(monkeypatch):
    monkeypatch.setattr(osm, "pause", lambda s: None)
    assert osm.wait_for_records(FakeSpace(), "sp-1", "bob", minimum=2, seconds=5) == 2


def test_forget_verified_keeps_watching_after_a_clean_pass_and_catches_a_late_extraction():
    """A turn stored seconds ago is not searchable, so the first pass finds nothing; its extraction writes a memory a moment later."""
    class Late(FakeSpace):
        def __init__(self):
            super().__init__()
            self.memories, self.events = [], []
            self.sleeps = 0

        def appear(self):  # the extraction lands once, while the first settle window runs
            if self.sleeps:
                return
            self.sleeps = 1
            self.memories = [{"memoryId": "late", "content": "The user's budget is 40 dollars.", "strategyName": "s",
                              "systemMetadata": {"createdAt": t(11, 0), "updatedAt": t(11, 0)}}]

    m = Late()

    def sleep(seconds):
        if seconds == 30:  # the settle window
            m.appear()

    out = osm.forget_shopper_verified(m, "sp-1", "bob", sleep=sleep, settle=30)
    assert out["left over"] == 0 and out["memories"] == 1 and m.memories == []


def test_without_a_settle_window_a_late_extraction_is_missed():
    class Late(FakeSpace):
        def __init__(self):
            super().__init__()
            self.memories, self.events = [], []

    m = Late()
    out = osm.forget_shopper_verified(m, "sp-1", "bob", sleep=lambda s: None)
    assert out == {"memories": 0, "events": 0, "sessions": 0, "left over": 0}  # the documented limit of settle=0


def test_a_stated_budget_is_read_from_the_shoppers_words_only_and_the_last_mention_wins():
    user = lambda t: {"role": "user", "text": t}  # noqa: E731
    assert osm.stated_budget([user("My budget is 30 dollars.")]) == 30
    assert osm.stated_budget([user("My budget is $40 now, my sister is chipping in. Which one?")]) == 40
    assert osm.stated_budget([user("budget of 25.50"), user("Actually the budget is 35")]) == 35
    assert osm.stated_budget([{"role": "assistant", "text": "The budget is 99 dollars"}]) is None  # the assistant's words do not count
    assert osm.stated_budget([user("a piggy bank under 30 dollars")]) is None  # a price ceiling is not a stated budget
    assert osm.budget_sentence(30) == "The user's budget is 30 dollars." and osm.budget_sentence(25.5) == "The user's budget is 25.5 dollars."


class FakeBudgetSpace(FakeSpace):
    def __init__(self, budgets=()):
        super().__init__()
        self.budgets = list(budgets)  # [{"memoryId", "content", "updatedAt"}]
        self.updated, self.made, self.removed = [], [], []

    def search_memories(self, **kw):
        self.calls.append(("search_memories", kw))
        return {"memories": [{"memoryId": b["memoryId"], "content": b["content"], "systemMetadata": {"updatedAt": b["updatedAt"]}}
                             for b in self.budgets]}

    def update_memory(self, **kw):
        self.updated.append(kw)

    def create_memory(self, **kw):
        self.made.append(kw)
        return {"memory": {"memoryId": "mem-new"}}

    def delete_memory(self, **kw):
        self.removed.append(kw["memoryId"])


def test_a_first_budget_is_written_directly_as_a_memory_tagged_as_the_budget():
    m = FakeBudgetSpace()
    assert osm.set_budget(m, "sp-1", "bob", 30) == "mem-new"
    made = m.made[0]
    assert made["content"] == "The user's budget is 30 dollars." and made["name"] == "shopper-facts" and made["actorId"] == "bob"
    assert made["metadata"] == {"kind": "budget"} and not m.updated
    filt = m.calls[0][1]["query"]["bool"]["filter"]
    assert filt == [{"term": {"metadata.kind": "budget"}}]  # the lookup is scoped to the budget memory (and the actor, by the service)


def test_a_changed_budget_updates_the_same_memory_in_place_and_keeps_one():
    m = FakeBudgetSpace([{"memoryId": "old", "content": "The user's budget is 30 dollars.", "updatedAt": t(9, 0)},
                         {"memoryId": "dup", "content": "The user's budget is 25 dollars.", "updatedAt": t(8, 0)}])
    assert osm.set_budget(m, "sp-1", "bob", 40) == "old"
    assert m.updated[0]["memoryId"] == "old" and m.updated[0]["content"] == "The user's budget is 40 dollars." and not m.made
    assert m.removed == ["dup"]  # two quick changes can leave a duplicate: the older one goes


def test_remember_turn_stores_the_event_and_writes_the_budget_only_when_one_is_stated():
    m = FakeBudgetSpace()
    osm.remember_turn(m, "sp-1", "bob", "sess", [{"role": "user", "text": "My budget is 40 dollars now."}, {"role": "assistant", "text": "ok"}])
    assert len(m.created) == 1 and m.made[0]["content"] == "The user's budget is 40 dollars."
    m2 = FakeBudgetSpace()
    osm.remember_turn(m2, "sp-1", "bob", "sess", [{"role": "user", "text": "I like green."}, {"role": "assistant", "text": "ok"}])
    assert len(m2.created) == 1 and not m2.made and not m2.updated

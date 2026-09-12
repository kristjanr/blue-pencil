"""The write-path invariant: a claim with no citation does not get written."""

import pytest

from bp.db import Graph
from bp.errors import UncitedClaim
from bp.models import Citation, Entity, Event, ObjectRecord, Promise, Report, Thread

CIT = [Citation(scene="LW1.01.1", quote="…")]


@pytest.mark.parametrize("record,write", [
    (Event(event_id="E", summary="x"), "write_event"),
    (Entity(entity_id="X", name="X"), "write_entity"),
    (ObjectRecord(object_id="O", name="O"), "write_object"),
    (Promise(promise_id="P", summary="p"), "write_promise"),
    (Thread(thread_id="T", name="t"), "write_thread"),
])
def test_uncited_claims_are_rejected(record, write):
    g = Graph(":memory:")
    with pytest.raises(UncitedClaim):
        getattr(g, write)(record)


def test_arrival_times_are_computed_at_write_time(world):
    """Extraction records that a message was sent; the engine works out when it
    lands. Nothing in the fixture states an arrival date."""
    graph, profile = world
    row = graph.conn.execute(
        "SELECT * FROM reports WHERE event_id='E-001' AND recipient='Ana'").fetchone()
    assert row["computed"] == 1
    assert row["arrives_text"] == ""            # nothing stated
    assert profile.calendar.format(row["arrive_day"]) == "2192-06-01"


def test_stated_arrival_beats_computation(world):
    """The author outranks our arithmetic."""
    graph, profile = world
    graph.write_event(Event(
        event_id="E-stated", summary="a message arrives early by some means",
        when="2180-06-01", where="Vela", observed_by=["Cyra"],
        reports=[Report(**{"from": "Cyra", "to": "Ana", "channel": "radio",
                           "departs": "2180-06-01", "arrives": "2181-01-01"})],
        citations=CIT))
    row = graph.conn.execute(
        "SELECT * FROM reports WHERE event_id='E-stated'").fetchone()
    assert row["computed"] == 0
    assert profile.calendar.format(row["arrive_day"]) == "2181-01-01"


def test_aliases_resolve_on_write(world):
    """Retrieval that misses a scene because the text says 'Anaïs' and the graph
    says 'Ana' is the most common silent failure in this design."""
    graph, _ = world
    graph.write_event(Event(
        event_id="E-alias", summary="the Cartographer files a chart", when="2185-01-01",
        where="Sol", observed_by=["the Cartographer"],
        reports=[Report(**{"from": "Anaïs", "to": "Borograve", "channel": "radio"})],
        citations=CIT))
    row = graph.conn.execute("SELECT * FROM reports WHERE event_id='E-alias'").fetchone()
    assert row["sender"] == "Ana" and row["recipient"] == "Boro"


def test_citations_are_queryable(world):
    graph, _ = world
    cites = graph.citations_for("event", "E-001")
    assert cites and cites[0].scene == "LW1.05.1"


def test_counts_cover_every_table(world):
    graph, _ = world
    counts = graph.counts()
    assert counts["events"] >= 8 and counts["reports"] >= 5 and counts["citations"] > 0

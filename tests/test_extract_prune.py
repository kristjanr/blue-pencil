"""The prune pass merges duplicates but never resolves a contradiction."""

from bp.extract import ExtractReport, audit_sample, prune
from bp.models import BeliefRecord, Citation, Event, ObjectRecord

CIT = [Citation(scene="LW1.01.1", quote="…")]


def test_duplicate_events_merge(world):
    graph, profile = world
    before = len(graph.events())
    for eid in ("E-dupe-a", "E-dupe-b"):
        graph.write_event(Event(event_id=eid, summary="The beacon at Sol is relit",
                                when="2186-01-01", where="Sol", observed_by=["Ana"], citations=CIT))
    prune(graph, profile, ExtractReport())
    assert len(graph.events()) == before + 1


def test_object_in_two_places_becomes_an_open_contradiction(world):
    graph, profile = world
    for oid, place in (("O-x1", "Sol"), ("O-x2", "Kepler")):
        graph.write_object(ObjectRecord(object_id=oid, name="Second Stone", location=place,
                                        as_of="2186-01-01", citations=CIT))
    report = ExtractReport()
    prune(graph, profile, report)
    assert report.contradictions >= 1
    open_ones = graph.contradictions(unresolved_only=True)
    assert any("Second Stone" in c["subject"] for c in open_ones)
    # Both readings survive. Nothing was silently picked.
    row = next(c for c in open_ones if "Second Stone" in c["subject"])
    assert "Sol" in row["reading_a"] + row["reading_b"]
    assert "Kepler" in row["reading_a"] + row["reading_b"]


def test_conflicting_beliefs_across_scenes_are_kept_open(world):
    """In a series with unreliable narrators the contradiction is often the story."""
    graph, profile = world
    scenes = [s.scene_id for s in graph.scenes()[:2]]
    assert len(scenes) == 2
    graph.write_event(Event(
        event_id="E-conflict", summary="the signal is a hoax", when="2186-01-01", where="Sol",
        observed_by=["Ana"],
        beliefs=[BeliefRecord(character="Boro", state="knows", as_of="2186-01-01"),
                 BeliefRecord(character="Boro", state="believes_false", as_of="2186-06-01")],
        citations=[Citation(scene=scenes[0], quote="a"), Citation(scene=scenes[1], quote="b")]))
    # Two scenes read this event differently — which is the case worth keeping open.
    graph.conn.execute(
        "UPDATE beliefs SET scene_id=? WHERE event_id='E-conflict' AND state='believes_false'",
        (scenes[1],))
    graph.commit()

    report = ExtractReport()
    prune(graph, profile, report)
    open_ones = graph.contradictions(unresolved_only=True)
    row = next((c for c in open_ones if "Boro" in c["subject"]), None)
    assert row is not None, "a cross-scene disagreement is a real contradiction"
    # Both sides must be able to point at the page they came from.
    assert row["cites_a"] not in ("", "[]", None)
    assert row["cites_b"] not in ("", "[]", None)


def test_a_disagreement_inside_one_scene_is_not_a_contradiction(world):
    """One scene asserting both states is an extraction slip, not the series being coy.

    Recording these buries the genuine cross-scene conflicts in noise — on the
    real corpus every single one of the 117 belief 'contradictions' was this.
    """
    graph, profile = world
    scene = graph.scenes()[0].scene_id
    graph.write_event(Event(
        event_id="E-samescene", summary="one scene, two readings", when="2186-01-01",
        where="Sol", observed_by=["Ana"],
        beliefs=[BeliefRecord(character="Zed", state="knows", as_of="2186-01-01"),
                 BeliefRecord(character="Zed", state="unaware", as_of="2186-01-01")],
        citations=[Citation(scene=scene, quote="a")]))
    graph.commit()

    prune(graph, profile, ExtractReport())
    assert not any("Zed" in c["subject"] for c in graph.contradictions(unresolved_only=True))


def test_audit_sample_is_reproducible(world):
    graph, _ = world
    a = audit_sample(graph, 10, seed=7)
    b = audit_sample(graph, 10, seed=7)
    assert a == b and len(a) == 10
    assert all(row["scene"] for row in a)

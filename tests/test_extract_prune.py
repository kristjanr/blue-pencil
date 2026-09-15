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


def test_separator_only_duplicates_merge(world):
    """`dr_carlisle` and `dr-carlisle` are one character written twice."""
    from bp.models import Entity

    graph, profile = world
    scene = graph.scenes()[0].scene_id
    for eid, desc in (("dr_carlisle", "the ship's doctor"), ("dr-carlisle", "")):
        graph.write_entity(Entity(entity_id=eid, name="Dr Carlisle", kind="character",
                                  description=desc,
                                  citations=[Citation(scene=scene, quote="q")]))
    graph.commit()

    report = ExtractReport()
    prune(graph, profile, report)
    left = [r[0] for r in graph.conn.execute(
        "SELECT entity_id FROM entities WHERE entity_id IN ('dr_carlisle','dr-carlisle')")]
    assert len(left) == 1, "one record should survive"
    assert report.entities_merged >= 1
    # the surviving record keeps the description that actually said something
    desc = graph.conn.execute(
        "SELECT description FROM entities WHERE entity_id=?", (left[0],)).fetchone()[0]
    assert desc == "the ship's doctor"


def test_distinguishing_ids_are_left_alone(world):
    """Two people sharing a first name must not be merged into one."""
    from bp.models import Entity

    graph, profile = world
    scene = graph.scenes()[0].scene_id
    for eid in ("kevin_dungeon_npc", "kevin_cryoeterna_rep"):
        graph.write_entity(Entity(entity_id=eid, name="Kevin", kind="character",
                                  citations=[Citation(scene=scene, quote="q")]))
    graph.commit()

    prune(graph, profile, ExtractReport())
    left = [r[0] for r in graph.conn.execute(
        "SELECT entity_id FROM entities WHERE entity_id LIKE 'kevin%'")]
    assert len(left) == 2, "name identity is not entity identity"


def test_merge_repoints_every_reference(world):
    """A merge that leaves a dangling id is worse than no merge."""
    from bp.models import Entity

    graph, profile = world
    scene = graph.scenes()[0].scene_id
    for eid in ("ship_alpha", "ship-alpha"):
        graph.write_entity(Entity(entity_id=eid, name="Alpha", kind="ship",
                                  citations=[Citation(scene=scene, quote="q")]))
    graph.write_event(Event(
        event_id="E-ref", summary="alpha arrives", when="2186-01-01", where="Sol",
        participants=["ship-alpha"], observed_by=["ship-alpha"],
        citations=[Citation(scene=scene, quote="q")]))
    graph.commit()

    prune(graph, profile, ExtractReport())
    keeper = graph.conn.execute(
        "SELECT entity_id FROM entities WHERE entity_id IN ('ship_alpha','ship-alpha')").fetchone()[0]
    row = graph.conn.execute(
        "SELECT participants, observed_by FROM events WHERE event_id='E-ref'").fetchone()
    import json as _json
    assert _json.loads(row["participants"]) == [keeper]
    assert _json.loads(row["observed_by"]) == [keeper]
    orphans = graph.conn.execute(
        "SELECT COUNT(*) FROM citations WHERE record_kind='entity' AND record_id NOT IN "
        "(SELECT entity_id FROM entities)").fetchone()[0]
    assert orphans == 0


def test_the_absorbed_id_still_resolves_after_a_merge(world):
    """The bug this test exists for: a merge rewrites every reference INSIDE
    the graph and then deletes the loser's row, but anything holding the old
    id from OUTSIDE the graph -- a citation in someone's notes, an eval
    fixture -- has nowhere left to look it up. `resolve_entity_id` and
    `entity()` must still find the merged-away id."""
    from bp.models import Entity

    graph, profile = world
    scene = graph.scenes()[0].scene_id
    for eid in ("ship_alpha", "ship-alpha"):
        graph.write_entity(Entity(entity_id=eid, name="Alpha", kind="ship",
                                  citations=[Citation(scene=scene, quote="q")]))
    graph.commit()

    prune(graph, profile, ExtractReport())
    keeper = graph.conn.execute(
        "SELECT entity_id FROM entities WHERE entity_id IN ('ship_alpha','ship-alpha')").fetchone()[0]
    loser = "ship_alpha" if keeper == "ship-alpha" else "ship-alpha"

    assert graph.resolve_entity_id(loser) == keeper
    assert graph.resolve_entity_id(keeper) == keeper, "an id that was never merged resolves to itself"
    assert graph.resolve_entity_id("never-existed") == "never-existed"
    assert graph.entity(loser)["entity_id"] == keeper


def test_resolve_entity_id_follows_a_chain_of_merges(world):
    """A twice-merged id (A -> B -> C) must resolve straight to the final,
    still-live id, not stop one hop short."""
    graph, _ = world
    graph.conn.execute("INSERT INTO entity_merges (old_id, new_id) VALUES ('a', 'b')")
    graph.conn.execute("INSERT INTO entity_merges (old_id, new_id) VALUES ('b', 'c')")
    graph.commit()
    assert graph.resolve_entity_id("a") == "c"


def test_resolve_entity_id_does_not_loop_forever_on_a_cycle():
    """Merge chains should never cycle, but a defensive lookup must not hang
    if bad data ever makes one."""
    import tempfile
    from pathlib import Path

    from bp.db import Graph

    with tempfile.TemporaryDirectory() as tmp:
        graph = Graph(Path(tmp) / "cycle.sqlite")
        graph.conn.execute("INSERT INTO entity_merges (old_id, new_id) VALUES ('a', 'b')")
        graph.conn.execute("INSERT INTO entity_merges (old_id, new_id) VALUES ('b', 'a')")
        graph.commit()
        assert graph.resolve_entity_id("a") in ("a", "b")
        graph.close()

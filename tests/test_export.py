"""The export is what anything outside `bp` sees, so it has to be honest.

Two properties matter more than the rest: it must not carry the corpus, and it
must not quietly lose entities to a name collision.
"""

import json

import pytest

from bp.export import Resolver, build_export, derive_edges, verify_no_corpus_text, write_export
from bp.models import Citation, Contradiction, Entity, Event


def _cit(scene="LW1.01.1"):
    return [Citation(scene=scene, quote="")]


def _export(world):
    graph, profile = world
    return build_export(graph, profile)


def test_a_fork_becomes_an_edge_even_though_no_table_holds_one(world):
    """`relationships` is empty; lineage lives in entities.parent_id."""
    graph, _ = world
    assert graph.conn.execute("SELECT count(*) FROM relationships").fetchone()[0] == 0

    edges = _export(world)["graph.json"]["edges"]
    forks = [e for e in edges if e["kind"] == "forked_from"]
    assert {(e["source"], e["target"]) for e in forks} == {("Ana-2", "Ana")}


def test_an_alias_resolves_to_the_entity_that_carries_it(world):
    """Events name people however the prose did — "the Cartographer", not "Ana"."""
    graph, profile = world
    assert Resolver(graph, profile).resolve("the Cartographer") == "Ana"


def test_an_unknown_reference_is_dropped_and_counted_not_invented(world):
    """A name with no entity behind it must not become a node."""
    graph, profile = world
    r = Resolver(graph, profile)
    assert r.resolve("Someone Who Is Not In This Book") is None
    assert r.unresolved["Someone Who Is Not In This Book"] == 1


def test_a_shared_name_resolves_to_the_most_cited_and_says_it_guessed(world):
    """Six Guppys is the real case. Dropping the name loses the character."""
    graph, profile = world
    for eid, scenes in [("Twin-A", 1), ("Twin-B", 4)]:
        graph.write_entity(Entity(entity_id=eid, name="Twin", kind="character",
                                  citations=_cit()))
        # Citation weight is the tie-break, so give one of them more of it.
        graph.conn.executemany(
            "INSERT INTO citations (record_kind, record_id, scene_id, quote)"
            " VALUES ('entity', ?, ?, '')",
            [(eid, f"LW1.0{i + 1}.1") for i in range(scenes)])
    graph.commit()

    r = Resolver(graph, profile)
    assert r.resolve("Twin") == "Twin-B", "the better-attested entity should win"
    assert r.ambiguous["twin"] == ["Twin-A", "Twin-B"], \
        "and the export must carry every candidate it could have been"


def test_a_pair_that_recurs_is_one_edge_carrying_its_weight():
    """Forty shared events are one relationship, not forty lines to draw."""
    events = [
        {"event_id": f"E-{i}", "participants": ["Ana", "Boro"], "observed_by": []}
        for i in range(40)
    ]
    edges = derive_edges([], events, [])
    assert len(edges) == 1
    assert edges[0]["count"] == 40
    assert len(edges[0]["via"]) == 8, "a sample of sources, not all forty"


def test_a_symmetric_edge_is_not_emitted_in_both_directions():
    """co_participant describes a pair; A-B and B-A are the same fact."""
    events = [
        {"event_id": "E-1", "participants": ["Ana", "Boro"], "observed_by": []},
        {"event_id": "E-2", "participants": ["Boro", "Ana"], "observed_by": []},
    ]
    edges = derive_edges([], events, [])
    assert len(edges) == 1 and edges[0]["count"] == 2


def test_observation_keeps_its_direction():
    """Who watched whom is not symmetric, and the export must not flatten it."""
    events = [{"event_id": "E-1", "participants": ["Ana"], "observed_by": ["Cyra"]}]
    edges = derive_edges([], events, [])
    assert [(e["source"], e["target"]) for e in edges] == [("Cyra", "Ana")]


def test_a_report_is_a_directed_edge_from_sender_to_recipient():
    """Who told whom is the route a belief travelled; it is not symmetric."""
    reports = [{"event_id": "E-1", "sender_entity": "Cyra", "recipient_entity": "Ana"}]
    edges = derive_edges([], [], [], (), reports)
    assert [(e["source"], e["target"], e["kind"]) for e in edges] \
        == [("Cyra", "Ana", "informed")]


def test_sharing_a_scene_is_not_the_same_edge_as_sending_word(world):
    """co_participant and informed must stay distinct — one implies a channel."""
    doc = _export(world)["graph.json"]
    kinds = {e["kind"] for e in doc["edges"]}
    assert "informed" in kinds
    assert "informed" in doc["edge_kinds"], "and it must say where it came from"


def test_a_channel_is_matched_to_one_the_profile_actually_declares(world):
    """Only a declared channel has a speed, and only a speed dates an arrival."""
    reports = _export(world)["detail.json"]["reports"]
    by_channel = {r["channel"]: r for r in reports}
    assert by_channel["radio"]["channel_declared"] == "radio"
    assert by_channel["radio"]["channel_instant"] is False
    assert by_channel["lattice"]["channel_instant"] is True


def test_an_undeclared_channel_says_unknown_rather_than_guessing(world):
    """90 free-text spellings, 2 declared channels. The gap has to be visible."""
    graph, profile = world
    graph.conn.execute("UPDATE reports SET channel='shouted across the hangar'"
                       " WHERE channel='radio'")
    graph.commit()

    doc = build_export(graph, profile)
    hops = doc["detail.json"]["reports"]
    shouted = [r for r in hops if r["channel"] == "shouted across the hangar"]
    assert shouted and all(r["channel_declared"] is None for r in shouted)
    assert all(r["channel_instant"] is None for r in shouted), \
        "an unknown speed is not the same as an instant one"

    quality = doc["graph.json"]["data_quality"]["channels"]
    assert quality["no_declared_channel"] == len(shouted)
    assert "radio" in quality["declared"]


def test_a_contradiction_carries_its_scenes_not_its_quotes(world):
    """cites_a holds whole citation records — quote text included."""
    graph, profile = world
    quote = graph.scenes()[0].text[:120]
    graph.write_contradiction(Contradiction(
        contradiction_id="C-1", subject="where Ana was",
        reading_a="on the station", reading_b="already aboard",
        citations_a=[Citation(scene="LW1.01.1", quote=quote)],
        citations_b=[Citation(scene="LW1.02.1", quote=quote)]))
    graph.commit()

    docs = build_export(graph, profile)
    found = docs["detail.json"]["contradictions"]
    assert [c["cites_a"] for c in found] == [["LW1.01.1"]]
    assert quote not in json.dumps(docs), "the quote must not ride along"


def test_nobody_is_related_to_themselves():
    events = [{"event_id": "E-1", "participants": ["Ana"], "observed_by": ["Ana"]}]
    assert derive_edges([], events, []) == []


def test_the_export_carries_no_scene_prose(world):
    """The whole point: this can be handed to a service the corpus must not reach."""
    graph, _ = world
    blobs = [json.dumps(doc) for doc in _export(world).values()]
    check = verify_no_corpus_text(graph, blobs)
    assert check.ok
    assert check.probes, "a clean result means nothing if the check looked at nothing"


def test_a_leak_of_scene_prose_is_caught_rather_than_written(world, tmp_path):
    """The guard has to fail on a real leak, or it proves nothing above."""
    graph, profile = world
    prose = graph.scenes()[0].text
    # A summary that copies the prose is exactly what a `SELECT *` on a new
    # column would do quietly. The export must refuse to write it.
    graph.write_event(Event(event_id="E-leak", summary=prose, when="2181-01-01",
                            where="Sol", observed_by=["Ana"], citations=_cit()))
    graph.commit()

    with pytest.raises(ValueError, match="verbatim book text"):
        write_export(graph, tmp_path, profile)
    assert not list(tmp_path.glob("*.json")), "nothing should be written on a refusal"


def test_a_guard_that_looked_at_nothing_does_not_report_clean(world):
    """The bug this project keeps finding: no evidence taken as evidence of none.

    An earlier version only probed scenes longer than 2,000 characters. Against
    a corpus of shorter scenes it built no probes, found no leak, and passed —
    which would have waved through an export that did carry the books.
    """
    graph, _ = world
    graph.conn.execute("UPDATE scenes SET text='short'")
    graph.conn.execute("DELETE FROM citations")
    graph.commit()
    assert verify_no_corpus_text(graph, ["anything at all"]).probes == 0, \
        "with nothing long enough to probe, the check must admit it, not pass"


def test_the_written_export_is_two_files_a_viewer_can_load(world, tmp_path):
    graph, profile = world
    written, check = write_export(graph, tmp_path, profile)
    assert set(written) == {"graph.json", "detail.json"}
    assert check.ok and check.probes

    doc = json.loads(written["graph.json"].read_text())
    assert doc["counts"]["entities"] == len(doc["entities"])
    assert doc["counts"]["edges"] == len(doc["edges"])
    assert set(doc["edge_kinds"]) >= {e["kind"] for e in doc["edges"]}, \
        "every edge kind drawn must explain where it came from"


def test_every_edge_endpoint_is_a_real_entity(world):
    """A viewer should never be handed an edge into nothing."""
    doc = _export(world)["graph.json"]
    ids = {e["entity_id"] for e in doc["entities"]}
    assert all(e["source"] in ids and e["target"] in ids for e in doc["edges"])

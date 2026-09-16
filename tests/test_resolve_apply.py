"""Applying entity-resolution verdicts: the merge primitive and the file format.

A wrong merge is the expensive mistake in this engine — it destroys a
distinction and leaves nothing to audit — so the rules that keep it honest are
pinned here: only positive verdicts move anything, re-running is safe, and
every reference follows the record it was pointing at.
"""

import json

import pytest

from bp.extract import merge_group
from bp.models import Citation, Entity, Event, Thread
from bp.resolve import apply_verdicts, load_verdicts

CIT = [Citation(scene="LW1.01.1", quote="q")]


def _two_records(graph):
    for eid, name in (("keeper", "Captain Hurricane"), ("loser", "The Captain")):
        graph.write_entity(Entity(entity_id=eid, name=name, kind="character", citations=CIT))
    graph.write_event(Event(
        event_id="E-m", summary="the captain acts", when="2186-01-01", where="Sol",
        participants=["loser"], observed_by=["loser"], citations=CIT))
    graph.write_thread(Thread(thread_id="T-m", name="the captain's arc", state="open",
                              characters=["loser"], citations=CIT))
    graph.commit()


def test_merge_group_repoints_every_reference(world):
    graph, _ = world
    _two_records(graph)

    assert merge_group(graph, "keeper", ["loser"]) == 1
    graph.commit()

    assert graph.conn.execute(
        "SELECT 1 FROM entities WHERE entity_id='loser'").fetchone() is None
    ev = graph.conn.execute(
        "SELECT participants, observed_by FROM events WHERE event_id='E-m'").fetchone()
    assert json.loads(ev["participants"]) == ["keeper"]
    assert json.loads(ev["observed_by"]) == ["keeper"]
    th = graph.conn.execute(
        "SELECT characters FROM threads WHERE thread_id='T-m'").fetchone()
    assert json.loads(th["characters"]) == ["keeper"]
    orphans = graph.conn.execute(
        "SELECT COUNT(*) FROM citations WHERE record_kind='entity' AND record_id NOT IN "
        "(SELECT entity_id FROM entities)").fetchone()[0]
    assert orphans == 0


def test_merge_group_keeps_the_absorbed_name_and_id_reachable(world):
    """The loser's name becomes an alias and its id becomes a redirect —
    otherwise anything that referred to it from outside the graph is stranded."""
    graph, _ = world
    _two_records(graph)
    merge_group(graph, "keeper", ["loser"])
    graph.commit()

    aliases = json.loads(graph.conn.execute(
        "SELECT aliases FROM entities WHERE entity_id='keeper'").fetchone()[0])
    assert "The Captain" in aliases
    assert graph.resolve_entity_id("loser") == "keeper"
    assert graph.entity("loser")["entity_id"] == "keeper"


def test_merge_group_is_idempotent(world):
    """Re-applying a verdicts file must not be destructive."""
    graph, _ = world
    _two_records(graph)
    assert merge_group(graph, "keeper", ["loser"]) == 1
    assert merge_group(graph, "keeper", ["loser"]) == 0
    graph.commit()


def test_merge_group_refuses_an_unknown_keeper(world):
    graph, _ = world
    _two_records(graph)
    with pytest.raises(ValueError, match="keeper"):
        merge_group(graph, "nobody-at-all", ["loser"])


def test_only_merge_and_partition_move_anything(tmp_path):
    """`split` says they are different people and `hold` says nobody has
    decided. Neither is evidence, so neither may absorb a record."""
    f = tmp_path / "v.yaml"
    f.write_text(
        "merge:\n"
        "  - {keep: a, absorb: [b, c], why: same}\n"
        "partition:\n"
        "  - {keep: d, absorb: [e], leave_alone: [f], why: mostly same}\n"
        "split:\n"
        "  - {members: [g, h], why: different people}\n"
        "hold:\n"
        "  - {members: [i, j], question: unsure}\n",
        encoding="utf-8")
    pairs = load_verdicts(f)
    assert pairs == [("a", ["b", "c"]), ("d", ["e"])]
    moved = {x for _, losers in pairs for x in losers}
    assert not moved & {"f", "g", "h", "i", "j"}


def test_apply_verdicts_dry_run_writes_nothing(world, tmp_path):
    graph, _ = world
    _two_records(graph)
    f = tmp_path / "v.yaml"
    f.write_text("merge:\n  - {keep: keeper, absorb: [loser], why: same}\n", encoding="utf-8")

    report = apply_verdicts(graph, f, apply=False)
    assert report.groups == 1 and report.absorbed == 1
    assert graph.conn.execute(
        "SELECT 1 FROM entities WHERE entity_id='loser'").fetchone() is not None

    report = apply_verdicts(graph, f, apply=True)
    assert report.absorbed == 1
    assert graph.conn.execute(
        "SELECT 1 FROM entities WHERE entity_id='loser'").fetchone() is None


def test_apply_verdicts_skips_a_group_already_applied(world, tmp_path):
    graph, _ = world
    _two_records(graph)
    f = tmp_path / "v.yaml"
    f.write_text("merge:\n  - {keep: keeper, absorb: [loser], why: same}\n", encoding="utf-8")
    apply_verdicts(graph, f, apply=True)

    again = apply_verdicts(graph, f, apply=True)
    assert again.groups == 0 and again.absorbed == 0
    assert again.already == ["keeper"]


def test_a_wrong_database_is_not_reported_as_already_applied(world, tmp_path):
    """The bug this test exists for: applying a verdicts file against a graph
    that has never held any of its records printed "every record already
    absorbed" for all 25 groups — indistinguishable from a finished job, and
    the reviewer nearly recorded the merge as done and went on to measure a
    graph that hadn't changed. Absent losers were read as absorbed before the
    keeper was ever checked."""
    graph, _ = world
    f = tmp_path / "v.yaml"
    f.write_text(
        "merge:\n"
        "  - {keep: nowhere_keeper, absorb: [nowhere_a, nowhere_b], why: same}\n"
        "  - {keep: also_missing, absorb: [nowhere_c], why: same}\n",
        encoding="utf-8")

    report = apply_verdicts(graph, f, apply=False)
    assert report.wrong_graph, "a graph holding none of these records is the wrong graph"
    assert report.already == [], "nothing here was ever absorbed"
    assert report.absorbed == 0
    assert str(graph.path) in report.render()
    assert "wrong database" in report.render()


def test_absence_alone_is_not_proof_of_absorption(world, tmp_path):
    """A loser that is simply gone, with no entity_merges row pointing at this
    keeper, is unexplained — not silently 'already done'."""
    graph, _ = world
    graph.write_entity(Entity(entity_id="keeper", name="Keeper", kind="character", citations=CIT))
    graph.commit()
    f = tmp_path / "v.yaml"
    f.write_text("merge:\n  - {keep: keeper, absorb: [vanished], why: same}\n", encoding="utf-8")

    report = apply_verdicts(graph, f, apply=False)
    assert report.already == []
    assert any("not recorded as merged here" in e for e in report.errors)


def test_a_genuinely_applied_group_is_reported_as_already_applied(world, tmp_path):
    graph, _ = world
    _two_records(graph)
    f = tmp_path / "v.yaml"
    f.write_text("merge:\n  - {keep: keeper, absorb: [loser], why: same}\n", encoding="utf-8")
    apply_verdicts(graph, f, apply=True)

    again = apply_verdicts(graph, f, apply=False)
    assert again.already == ["keeper"], "entity_merges proves this one really was absorbed"
    assert not again.wrong_graph and not again.errors

"""The local viewer: it must not be able to write, and it must not disagree.

Two failure modes are worth a test each. A viewer that can write is a viewer you
have to back up before running. A viewer that recomputes the epistemic rules its
own way is worse than no viewer, because it will confidently contradict
`bp check` and there will be no way to tell which one is right from the outside.
"""

from __future__ import annotations

import sqlite3

import pytest

import synthetic
from bp.knowledge import KnowledgeGraph
from bp.viz import ReadOnlyGraph, citations_for, event_detail, path_of, search, summary


@pytest.fixture
def ro(tmp_path):
    """A built synthetic world, reopened through the read-only window."""
    db = tmp_path / "longwater.sqlite"
    graph, profile = synthetic.build(db)
    graph.commit()
    graph.close()
    g = ReadOnlyGraph(db, synthetic.make_profile())
    yield g, profile, db
    g.close()


def test_the_connection_cannot_write(ro):
    """The guarantee is the connection string, not our good intentions."""
    g, _, _ = ro
    for sql in ("INSERT INTO meta(key,value) VALUES('x','y')",
                "UPDATE entities SET name='tampered'",
                "DELETE FROM events",
                "DROP TABLE beliefs"):
        with pytest.raises(sqlite3.OperationalError):
            g.conn.execute(sql)
    # and the data is untouched
    assert g.conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] > 0


def test_reading_does_not_need_the_schema_written(ro):
    """Opening must not take a write lock, so it works while extraction runs."""
    g, _, db = ro
    holder = sqlite3.connect(db)
    holder.execute("BEGIN IMMEDIATE")          # simulate a run holding the write lock
    try:
        assert summary(g)["counts"]["events"] > 0   # still readable
    finally:
        holder.rollback()
        holder.close()


def test_path_agrees_with_the_engine(ro):
    """The viewer must return what KnowledgeGraph returns — not its own reading."""
    g, _, _ = ro
    kg = KnowledgeGraph(g)
    for event_id in ("E-001", "E-002", "E-005", "E-007"):
        for who in ("Ana", "Boro", "Cyra"):
            direct = kg.earliest_knowledge(event_id, who)
            served = path_of(g, event_id, who)
            assert served["day"] == direct.day
            assert served["reachable"] == direct.reachable
            assert len(served["hops"]) == len(direct.path)
            assert served["explain"] == kg.explain(direct)


def test_light_lag_survives_the_round_trip(ro):
    """Sol to Vela is twelve light years; no viewer gets to soften that."""
    g, profile, _ = ro
    p = path_of(g, "E-001", "Ana")          # Cyra reports from Vela by radio
    assert p["reachable"]
    event_day = g.event("E-001")["day_lo"]
    assert p["day"] - event_day == pytest.approx(12 * 365.25, rel=0.02)
    hop = [h for h in p["hops"] if h["kind"] == "report"][-1]
    assert hop["channel"] == "radio"
    assert hop["computed"] is True          # arrival derived from the space model


def test_computed_and_stated_arrivals_stay_distinguishable(ro):
    """`computed` is the field that makes the check deterministic; keep it typed."""
    g, _, _ = ro
    hops = [h for e in ("E-001", "E-002", "E-006", "E-007")
            for h in path_of(g, e, "Ana")["hops"]]
    assert hops, "the fixture should produce at least one hop"
    assert all(isinstance(h["computed"], bool) for h in hops)


def test_channel_dates_are_restored_from_meta(tmp_path):
    """Without this the viewer answers with an instant relay available from book one."""
    from bp.timeline import Span

    db = tmp_path / "lw.sqlite"
    graph, profile = synthetic.build(db)
    lattice_day = profile.calendar.parse("2190-01-01").lo
    graph.set_meta("channel_from:lattice", str(lattice_day))
    graph.commit()
    graph.close()

    fresh = synthetic.make_profile()
    for ch in fresh.channels:            # nothing resolved yet
        ch.available_from_date = None
    g = ReadOnlyGraph(db, fresh)
    try:
        lattice = [c for c in g.profile.channels if c.name == "lattice"][0]
        assert lattice.available_from_date is not None
        assert lattice.available_from_date.lo == pytest.approx(lattice_day)
    finally:
        g.close()


def test_citations_carry_the_quote_and_say_whether_it_is_real(ro):
    """The audit question the export cannot carry: is the line actually there?"""
    g, _, _ = ro
    row = g.conn.execute(
        "SELECT record_kind, record_id FROM citations WHERE quote != '' LIMIT 1").fetchone()
    if row is None:
        pytest.skip("fixture has no quoted citations")
    cites = citations_for(g, row["record_kind"], row["record_id"])
    assert cites
    for c in cites:
        assert "quote_found" in c and isinstance(c["quote_found"], bool)
        if c["quote_found"]:
            assert c["quote"] in c["excerpt"]


def test_a_fabricated_quote_is_reported_as_missing(ro, tmp_path):
    """A record that cannot prove itself is the finding, not a rendering detail."""
    g, _, db = ro
    rw = sqlite3.connect(db)
    rw.execute("INSERT INTO citations(record_kind,record_id,scene_id,quote)"
               " VALUES('event','E-001',?,?)",
               (g.conn.execute("SELECT scene_id FROM scenes LIMIT 1").fetchone()["scene_id"],
                "a line that appears nowhere in the corpus"))
    rw.commit(); rw.close()
    cites = citations_for(g, "event", "E-001")
    assert any(c["quote_found"] is False for c in cites)


def test_event_detail_unpacks_json_columns(ro):
    g, _, _ = ro
    d = event_detail(g, "E-001")
    assert isinstance(d["participants"], list)
    assert isinstance(d["observed_by"], list)
    assert isinstance(d["reports"], list)
    assert isinstance(d["beliefs"], list)


def test_summary_counts_reports_with_an_arrival(ro):
    """The number that says whether an information path is answerable at all."""
    g, _, _ = ro
    s = summary(g)
    assert s["counts"]["reports"] >= s["reports_with_arrival"] >= 0


def test_search_finds_by_name_and_summary(ro):
    g, _, _ = ro
    assert any(e["name"] == "Ana" for e in search(g, "Ana")["entities"])
    assert any("relay" in e["summary"] for e in search(g, "relay")["events"])


def test_missing_event_is_indeterminate_not_a_crash(ro):
    g, _, _ = ro
    p = path_of(g, "E-nope", "Ana")
    assert p["indeterminate"] and not p["reachable"]

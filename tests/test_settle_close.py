"""Writing a close, and the two ways a promise can be marked paid wrongly.

Both were found by auditing the ledger rather than by a failing run, which is
the tell: a close that names the wrong scene and a promise that was never open
both look perfectly well-formed in the table.
"""

from bp.extract import ExtractReport, _write_records
from bp.models import Citation, Promise
from bp.settle import SettleReport, _close, audit_paid_promises


def _promise(graph, scenes, pid="P-1"):
    graph.write_promise(Promise(
        promise_id=pid, summary="a debt", planted_in=[scenes[0]], owed_by=["Ana"],
        citations=[Citation(scene=scenes[0], quote="q")]))
    graph.commit()


def _status(graph, pid="P-1"):
    return dict(graph.conn.execute(
        "SELECT status, paid_in, paid_quote FROM promises WHERE promise_id=?",
        (pid,)).fetchone())


def test_the_earliest_payoff_wins_whatever_order_the_closes_arrive_in(world):
    """A batch returns scenes unordered, so both orders must land the same way."""
    graph, _ = world
    scenes = [s.scene_id for s in graph.scenes()]
    early, late = scenes[1], scenes[4]

    for first, second in ((early, late), (late, early)):
        graph.conn.execute("DELETE FROM promises")
        graph.conn.execute("DELETE FROM record_changes")
        _promise(graph, scenes)
        _close(graph, "P-1", first, "run", 0.9, "first quote")
        _close(graph, "P-1", second, "run", 0.9, "second quote")
        assert _status(graph)["paid_in"] == early, \
            f"arriving {first} then {second} still has to keep the earlier scene"


def test_a_superseded_close_says_which_scene_already_held_it(world):
    """The caller has to be able to count these, not just be silently ignored."""
    graph, _ = world
    scenes = [s.scene_id for s in graph.scenes()]
    _promise(graph, scenes)

    assert _close(graph, "P-1", scenes[1], "run", 0.9, "q") == ""
    assert _close(graph, "P-1", scenes[4], "run", 0.9, "q") == scenes[1]


def test_a_close_that_cannot_be_shown_to_be_earlier_leaves_the_record_alone(world):
    """An unplaceable scene has no position, so it cannot win the comparison."""
    graph, _ = world
    scenes = [s.scene_id for s in graph.scenes()]
    _promise(graph, scenes)
    _close(graph, "P-1", scenes[2], "run", 0.9, "real quote")

    assert _close(graph, "P-1", "B9.99.9", "run", 0.9, "from nowhere") == scenes[2]
    assert _status(graph)["paid_quote"] == "real quote"


def test_re_closing_the_same_scene_still_fills_in_the_evidence(world):
    """The guard must not block a re-run improving a quoteless legacy close."""
    graph, _ = world
    scenes = [s.scene_id for s in graph.scenes()]
    _promise(graph, scenes)
    _close(graph, "P-1", scenes[2], "run", 0.9, "")

    assert _close(graph, "P-1", scenes[2], "run", 0.9, "the line that paid it") == ""
    assert _status(graph)["paid_quote"] == "the line that paid it"


def test_two_scenes_closing_one_promise_is_reported_not_just_resolved():
    """Also the measurement of what dropping-as-closed saves in listing tokens."""
    report = SettleReport(closes=[
        ("S-1", "P-1", "why", 0.9, "q"),
        ("S-4", "P-1", "why", 0.9, "q"),
        ("S-2", "P-2", "why", 0.9, "q"),
    ])
    assert report.double_closed() == [("P-1", ["S-1", "S-4"])]
    assert "P-1" in report.render()


def _paid_by_hand(graph, pid, scene_id, quote, *, trail=True):
    """What a correction script does: straight into the table, past `_close`."""
    graph.conn.execute(
        "UPDATE promises SET status='paid', paid_in=?, paid_quote=? WHERE promise_id=?",
        (scene_id, quote, pid))
    if trail:
        graph.record_change(table="promises", record_id=pid, field="status",
                            old="open", new="paid", run_id="script", reason="ruled by hand")
    graph.commit()


def test_a_close_written_straight_into_the_table_still_has_to_prove_itself(world):
    """The write-path guard sees none of this; the audit reads what is stored."""
    graph, _ = world
    scene = graph.scenes()[2]
    _promise(graph, [s.scene_id for s in graph.scenes()])
    _paid_by_hand(graph, "P-1", scene.scene_id, scene.text[:60])

    audit = audit_paid_promises(graph)
    assert audit.paid == 1 and audit.failures == 0
    assert "all three invariants hold" in audit.render()


def test_a_quote_that_no_longer_appears_in_its_scene_is_caught(world):
    """A quote is verified when stored and never again.

    The drop-cap repair rewrote the opening line of hundreds of scenes. Any
    close whose evidence lived in one of those lines went stale silently, and
    nothing else in the system would ever look.
    """
    graph, _ = world
    scene = graph.scenes()[2]
    _promise(graph, [s.scene_id for s in graph.scenes()])
    _paid_by_hand(graph, "P-1", scene.scene_id, scene.text[:60])
    assert audit_paid_promises(graph).failures == 0, "clean before the corpus moves"

    graph.conn.execute("UPDATE scenes SET text=? WHERE scene_id=?",
                       ("something else entirely, at length, with other words in it",
                        scene.scene_id))
    graph.commit()

    audit = audit_paid_promises(graph)
    assert [p for p, _s in audit.quote_not_in_scene] == ["P-1"]
    assert "LEDGER AUDIT FAILED" in audit.render()


def test_a_close_with_no_quote_or_no_trail_is_caught(world):
    graph, _ = world
    scenes = [s.scene_id for s in graph.scenes()]
    _promise(graph, scenes, "P-quoteless")
    _promise(graph, scenes, "P-trailless")
    _paid_by_hand(graph, "P-quoteless", scenes[2], "")
    _paid_by_hand(graph, "P-trailless", scenes[2], graph.scenes()[2].text[:60], trail=False)

    audit = audit_paid_promises(graph)
    assert audit.no_quote == ["P-quoteless"]
    assert audit.no_trail == ["P-trailless"]


def test_a_paid_in_naming_no_scene_is_not_silently_skipped(world):
    """An unresolvable scene reference cannot be checked, so it must be reported."""
    graph, _ = world
    _promise(graph, [s.scene_id for s in graph.scenes()])
    _paid_by_hand(graph, "P-1", "B9.99.9", "a quote from nowhere")

    audit = audit_paid_promises(graph)
    assert [p for p, _s in audit.scene_missing] == ["P-1"]


def test_a_pass_says_which_books_its_evidence_came_from(world):
    """A clean result is only clean about the ground it covered.

    The real ledger holds 354 of its 358 quotes in books 1 and 2, and four
    across books 3-5. Reading that pass as clearing a hazard in book 5 is the
    house bug wearing a green check: no evidence there for the check to fail
    on. The total invites the mistake, so the distribution is printed with it.
    """
    graph, _ = world
    scenes = graph.scenes()
    books = {s.book_id for s in scenes}
    assert len(books) > 1, "the fixture needs two books for this to mean anything"

    lopsided = sorted(books)[0]
    for i, scene in enumerate(s for s in scenes if s.book_id == lopsided):
        _promise(graph, [scene.scene_id], f"P-{i}")
        _paid_by_hand(graph, f"P-{i}", scene.scene_id, scene.text[:60])

    audit = audit_paid_promises(graph)
    assert audit.failures == 0
    assert set(audit.by_book) == {lopsided}, "every quote sits in the one book"
    rendered = audit.render()
    assert f"{lopsided} {audit.paid}" in rendered, "the pass must say where it looked"


def test_a_ledger_with_nothing_paid_does_not_report_clean(world):
    """The house bug: every clause passes because nothing was examined."""
    graph, _ = world
    graph.conn.execute("UPDATE promises SET status='open'")
    graph.commit()

    audit = audit_paid_promises(graph)
    assert audit.failures == 0 and audit.paid == 0
    assert "this is not a pass" in audit.render(), \
        "a check that looked at nothing must say so, not pass"


def test_extraction_cannot_hand_back_a_promise_already_paid(world):
    """It has no quote from a paying scene, so it has not settled anything.

    40 of the 41 found in the ledger were "paid" in their own planting scene,
    with no record_changes trail behind them at all.
    """
    graph, _ = world
    scene_id = graph.scenes()[0].scene_id
    payload = type("Ledger", (), {"objects": [], "threads": [], "promises": [Promise(
        promise_id="P-born-paid", summary="a debt discharged on the spot",
        planted_in=[scene_id], status="paid", paid_in=scene_id,
        paid_quote="never verified against anything",
        citations=[Citation(scene=scene_id, quote="q")])]})()

    report = ExtractReport()
    _write_records(graph, scene_id, "ledger", payload, report)
    graph.commit()

    assert report.promises_born_paid == 1
    assert _status(graph, "P-born-paid") == {
        "status": "open", "paid_in": "", "paid_quote": ""}, \
        "reopened, not dropped: 15 of the 41 were real debts that had left the ledger"
    assert "extraction returned them paid" in report.render()

"""Writing a close, and the two ways a promise can be marked paid wrongly.

Both were found by auditing the ledger rather than by a failing run, which is
the tell: a close that names the wrong scene and a promise that was never open
both look perfectly well-formed in the table.
"""

from bp.extract import ExtractReport, _write_records
from bp.models import Citation, Promise
from bp.settle import SettleReport, _close


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

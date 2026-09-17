"""Stage 1 of settling the ledger: which scenes could pay off which promises.

The rules pinned here were each paid for in a mistake. Over-include rather than
drop, because a promise nothing can filter must still be examined. Do not
narrow by book, because a filter that misses a cross-book payoff does not
merely miss it — combined with deriving scope from what survives the pass, it
relabels that promise a series-level debt the continuation has to discharge.
"""

import pytest
from types import SimpleNamespace

from bp.models import Citation, Entity, Promise
from bp.settle import candidate_index

CIT = [Citation(scene="LW1.01.1", quote="q")]


def _scene_ids(world):
    graph, _ = world
    return [s.scene_id for s in graph.scenes()]


def test_a_promise_is_only_a_candidate_for_scenes_after_its_plant(world):
    graph, profile = world
    scenes = _scene_ids(world)
    graph.write_entity(Entity(entity_id="ana", name="Ana", kind="character", citations=CIT))
    graph.write_promise(Promise(
        promise_id="P-late", summary="something owed", planted_in=[scenes[2]],
        owed_by=["Ana"], citations=[Citation(scene=scenes[2], quote="q")]))
    graph.commit()

    index, _ = candidate_index(graph, profile)
    assert "P-late" not in index[scenes[0]], "a scene before the plant cannot pay it"
    assert "P-late" not in index[scenes[2]], "nor the plant scene itself"
    later = [s for s in scenes[3:] if "P-late" in index[s]]
    assert later, "some later scene the debtor appears in must be a candidate"


def test_a_promise_owed_to_nobody_resolvable_is_over_included(world):
    """The rule this test exists for: filtered strictly, a promise owed by "the
    narrative" or by a group gets an empty candidate list and is silently never
    examined — indistinguishable from examined and found unpaid. 446 promises
    in the real graph are in that position."""
    graph, profile = world
    scenes = _scene_ids(world)
    graph.write_promise(Promise(
        promise_id="P-nobody", summary="the narrative owes the reader this",
        planted_in=[scenes[0]], owed_by=["the narrative"],
        citations=[Citation(scene=scenes[0], quote="q")]))
    graph.commit()

    index, report = candidate_index(graph, profile)
    assert report.unowned >= 1
    assert all("P-nobody" in index[s] for s in scenes[1:]), \
        "an unfilterable promise goes in every later scene, never none"


def test_a_promise_with_no_usable_plant_is_still_examined(world):
    """49 promises in the real graph name a plant scene that does not exist.
    Losing the anchor must not mean losing the promise."""
    graph, profile = world
    scenes = _scene_ids(world)
    graph.write_promise(Promise(
        promise_id="P-unanchored", summary="planted nowhere findable",
        planted_in=["book9.99.9"], owed_by=[],
        citations=[Citation(scene=scenes[0], quote="q")]))
    graph.commit()

    index, report = candidate_index(graph, profile)
    assert report.unanchored >= 1
    assert all("P-unanchored" in index[s] for s in scenes), \
        "with no anchor every scene is potentially after it"


def test_candidates_are_not_confined_to_the_plants_own_book(world):
    """Confining a payoff to its own book declares unpayable the promises owed
    by characters who go on appearing — and, because scope is derived from what
    survives the pass, silently relabels them series-level debts."""
    graph, profile = world
    books = {}
    for s in graph.scenes():
        books.setdefault(s.book_id, []).append(s.scene_id)
    assert len(books) > 1, "fixture needs more than one book for this to mean anything"

    first_book, later_book = list(books)[0], list(books)[1]
    graph.write_entity(Entity(entity_id="ana", name="Ana", kind="character", citations=CIT))
    graph.write_promise(Promise(
        promise_id="P-crossbook", summary="paid off much later", planted_in=[books[first_book][0]],
        owed_by=["Ana"], citations=[Citation(scene=books[first_book][0], quote="q")]))
    graph.commit()

    index, _ = candidate_index(graph, profile)
    assert any("P-crossbook" in index[s] for s in books[later_book]), \
        "a later book must be able to pay an earlier book's promise"


def test_a_closed_promise_is_not_a_candidate(world):
    graph, profile = world
    scenes = _scene_ids(world)
    graph.write_promise(Promise(
        promise_id="P-done", summary="already settled", planted_in=[scenes[0]],
        status="paid", paid_in=scenes[1], owed_by=[],
        citations=[Citation(scene=scenes[0], quote="q")]))
    graph.commit()

    index, _ = candidate_index(graph, profile)
    assert all("P-done" not in pids for pids in index.values())


def test_the_estimate_counts_each_scene_once(world):
    """The whole point of asking per scene rather than per pair: the expensive
    context is the scene text, and it is sent once per call, not once per
    candidate."""
    graph, profile = world
    _, report = candidate_index(graph, profile)
    scene_tokens = sum(r[0] or 0 for r in graph.conn.execute("SELECT tokens FROM scenes"))
    assert report.prompt_tokens >= scene_tokens
    assert report.prompt_tokens < scene_tokens * report.scenes, \
        "per-pair would multiply the scene text by the candidate count"


# ------------------------------------------------------------------ stage two
class _FakeMessages:
    def __init__(self, closes_by_call):
        self._script = list(closes_by_call)
        self.calls = []

    def create(self, **kw):
        from types import SimpleNamespace
        self.calls.append(kw)
        payload = self._script.pop(0) if self._script else {"closes": []}
        return SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", input=payload)],
            stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=50,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0),
        )


class _FakeClient:
    def __init__(self, closes_by_call):
        self.messages = _FakeMessages(closes_by_call)


def _open_promise(graph, pid, scene, owed=()):
    graph.write_promise(Promise(promise_id=pid, summary=f"{pid} will be paid off",
                                planted_in=[scene], owed_by=list(owed),
                                citations=[Citation(scene=scene, quote="q")]))


def test_a_close_whose_quote_is_not_in_the_scene_is_rejected(world):
    """The same exact test `bp ground` uses, applied before anything is
    written. A quote that isn't in the scene it names was invented — 391
    records in the real graph failed exactly this."""
    from bp.settle import settle

    graph, profile = world
    scenes = _scene_ids(world)
    _open_promise(graph, "P-1", scenes[0])
    graph.commit()

    client = _FakeClient([{"closes": [
        {"promise_id": "P-1", "quote": "a line that appears nowhere in this corpus", "why": "x"}]}])
    report = settle(graph, profile, client, model="claude-sonnet-5", max_usd=5.0)

    assert report.closes == [], "an unverifiable quote closes nothing"
    assert report.rejected_quote, "and it is reported rather than silently dropped"
    assert graph.conn.execute(
        "SELECT status FROM promises WHERE promise_id='P-1'").fetchone()[0] == "open"


def test_a_close_naming_a_promise_that_was_not_offered_is_rejected(world):
    from bp.settle import settle

    graph, profile = world
    scenes = _scene_ids(world)
    _open_promise(graph, "P-1", scenes[0])
    graph.commit()

    real_line = " ".join(graph.scene(scenes[1]).text.split()[:6])
    client = _FakeClient([{"closes": [
        {"promise_id": "P-not-offered", "quote": real_line, "why": "x"}]}])
    report = settle(graph, profile, client, model="claude-sonnet-5", max_usd=5.0)

    assert report.closes == []
    assert report.rejected_unknown


def test_a_verified_close_is_only_written_when_applied(world):
    from bp.settle import settle

    graph, profile = world
    scenes = _scene_ids(world)
    _open_promise(graph, "P-1", scenes[0])
    graph.commit()
    line = " ".join(graph.scene(scenes[1]).text.split()[:8])

    dry = settle(graph, profile, _FakeClient([{"closes": [
        {"promise_id": "P-1", "quote": line, "why": "paid here"}]}]),
        model="claude-sonnet-5", max_usd=5.0)
    assert len(dry.closes) == 1
    assert graph.conn.execute(
        "SELECT status FROM promises WHERE promise_id='P-1'").fetchone()[0] == "open"

    wet = settle(graph, profile, _FakeClient([{"closes": [
        {"promise_id": "P-1", "quote": line, "why": "paid here"}]}]),
        model="claude-sonnet-5", max_usd=5.0, apply=True)
    assert len(wet.closes) == 1
    row = graph.conn.execute(
        "SELECT status, paid_in FROM promises WHERE promise_id='P-1'").fetchone()
    assert row["status"] == "paid" and row["paid_in"]


def test_a_closed_promise_is_dropped_from_later_scenes(world):
    """Lever 3: a promise only needs paying once, and the earliest payoff is
    the right one. Dropping it is both free accuracy and the only lossless way
    to shrink the listing, which is 96% of what a run costs."""
    from bp.settle import settle

    graph, profile = world
    scenes = _scene_ids(world)
    _open_promise(graph, "P-1", scenes[0])
    graph.commit()
    line = " ".join(graph.scene(scenes[1]).text.split()[:8])

    client = _FakeClient([{"closes": [{"promise_id": "P-1", "quote": line, "why": "paid"}]}])
    settle(graph, profile, client, model="claude-sonnet-5", max_usd=5.0)

    later = [kw for kw in client.messages.calls[1:]]
    assert all("P-1" not in kw["messages"][0]["content"] for kw in later), \
        "once closed it must not be offered to any later scene"


def test_the_spend_cap_stops_the_run(world):
    """The cap lives here because this path does not go through `extract`,
    where the existing one is."""
    from bp.settle import settle

    graph, profile = world
    scenes = _scene_ids(world)
    for i, s in enumerate(scenes[:3]):
        _open_promise(graph, f"P-{i}", s)
    graph.commit()

    report = settle(graph, profile, _FakeClient([]), model="claude-sonnet-5", max_usd=0.0)
    assert report.stopped and "cap" in report.stopped
    assert report.scenes_read == 0


def test_an_escaped_apostrophe_does_not_read_as_a_fabricated_quote(world):
    """The bug this test exists for: the model quotes faithfully, but the
    escape sometimes arrives as six literal characters, and prose is full of
    curly apostrophes. That rejected 5 of 6 proposed closes in the pilot — the
    verification guard manufacturing the very fabrication it exists to catch."""
    from bp.settle import settle

    graph, profile = world
    scenes = _scene_ids(world)
    _open_promise(graph, "P-1", scenes[0])
    graph.conn.execute("UPDATE scenes SET text=? WHERE scene_id=?",
                       ("She said she can’t forget the relay.", scenes[1]))
    graph.commit()

    escaped = "she can" + chr(92) + "u2019t forget the relay"
    report = settle(graph, profile, _FakeClient([{"closes": [
        {"promise_id": "P-1", "quote": escaped, "why": "paid"}]}]),
        model="claude-sonnet-5", max_usd=5.0)
    assert len(report.closes) == 1, "an escaped apostrophe is still a real quote"
    assert not report.rejected_quote

    report = settle(graph, profile, _FakeClient([{"closes": [
        {"promise_id": "P-1", "quote": "a line that is nowhere in this scene", "why": "x"}]}]),
        model="claude-sonnet-5", max_usd=5.0)
    assert report.closes == [], "and a quote that really is absent still fails"


def test_closing_a_promise_records_what_it_overwrote(world):
    """We will correct this settler's prompt at least once, and then want to
    know which closes came from the run before the fix."""
    from bp.settle import settle

    graph, profile = world
    scenes = _scene_ids(world)
    _open_promise(graph, "P-1", scenes[0])
    graph.commit()
    line = " ".join(graph.scene(scenes[1]).text.split()[:8])

    settle(graph, profile, _FakeClient([{"closes": [
        {"promise_id": "P-1", "quote": line, "why": "paid"}]}]),
        model="claude-sonnet-5", max_usd=5.0, apply=True, run_id="settle-under-test")

    changes = {c["field"]: c for c in graph.changes_for("promises", "P-1")}
    assert changes["status"]["old_value"] == "open"
    assert changes["status"]["new_value"] == "paid"
    assert changes["paid_in"]["new_value"] == scenes[1]
    assert graph.changes_in_run("settle-under-test")


def test_a_dry_run_leaves_no_trail(world):
    from bp.settle import settle

    graph, profile = world
    scenes = _scene_ids(world)
    _open_promise(graph, "P-1", scenes[0])
    graph.commit()
    line = " ".join(graph.scene(scenes[1]).text.split()[:8])

    settle(graph, profile, _FakeClient([{"closes": [
        {"promise_id": "P-1", "quote": line, "why": "paid"}]}]),
        model="claude-sonnet-5", max_usd=5.0, run_id="dry-under-test")
    assert graph.changes_in_run("dry-under-test") == []


def test_excluding_closed_promises_shrinks_every_later_listing(world):
    """How the saving from settling in reading order gets measured before it is
    paid for. A promise closed early stops appearing in every later scene, and
    the listing is 96% of what a run costs."""
    graph, profile = world
    scenes = _scene_ids(world)
    for i in range(3):
        _open_promise(graph, f"P-{i}", scenes[0])
    graph.commit()

    full, rep_full = candidate_index(graph, profile)
    part, rep_part = candidate_index(graph, profile, exclude={"P-0", "P-1"})

    assert rep_part.promises == rep_full.promises - 2
    assert rep_part.pairs < rep_full.pairs
    assert rep_part.prompt_tokens < rep_full.prompt_tokens
    assert all("P-0" not in pids for pids in part.values())
    assert any("P-2" in pids for pids in part.values()), "the rest are untouched"


# --------------------------------------------------------------- cost estimate
def test_output_tokens_are_priced_at_the_output_rate():
    """The bug this exists for: output was folded into a single scalar on the
    input term, which hid the fact that it bills at five times input. Fold it
    back and this test fails."""
    from bp.settle import IndexReport, _OUTPUT_TOKENS_PER_CALL

    rep = IndexReport(scenes=100, prompt_tokens=1_000_000)
    in_only = rep.cost_usd(2.0, batch=False)
    with_out = rep.cost_usd(2.0, 10.0, batch=False)

    assert with_out > in_only
    assert with_out - in_only == pytest.approx(100 * _OUTPUT_TOKENS_PER_CALL / 1e6 * 10.0)


def test_the_estimate_reproduces_the_one_run_that_was_actually_billed():
    """Book one: 159 scenes, 780,270 predicted input tokens on sonnet, and a
    real invoice of $4.71. The constants are fitted to exactly this point, so
    if a later change moves the prediction off it, the change either found a
    better model of the cost — and should re-fit — or broke this one."""
    from bp.settle import IndexReport

    rep = IndexReport(scenes=159, prompt_tokens=780_270)
    assert rep.cost_usd(2.0, 10.0, batch=False) == pytest.approx(4.71, abs=0.05)
    # and the batch price is half of it, not half of some other arithmetic
    assert rep.cost_usd(2.0, 10.0, batch=True) == pytest.approx(
        rep.cost_usd(2.0, 10.0, batch=False) / 2)


def test_a_run_records_the_tokens_that_would_let_the_next_estimate_be_better():
    """Book one's bill could not be decomposed because the run kept only the
    dollar total — a product of six terms, which constrains none of them."""
    from bp.llm import Usage
    from bp.settle import SettleReport, _record_spend

    usage = Usage()
    usage.add("claude-sonnet-5", SimpleNamespace(
        input_tokens=1000, output_tokens=100,
        cache_read_input_tokens=0, cache_creation_input_tokens=0), stage="settle")

    rep = SettleReport(estimated_input_tokens=500)
    _record_spend(rep, usage, "claude-sonnet-5")
    rep.usd = usage.usd

    assert rep.as_dict()["spend"]["input_tokens"] == 1000
    assert rep.as_dict()["spend"]["output_tokens"] == 100
    assert rep.as_dict()["spend"]["estimated_input_tokens"] == 500
    assert "2.00x" in rep.calibration(), "1000 billed against 500 predicted"

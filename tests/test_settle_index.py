"""Stage 1 of settling the ledger: which scenes could pay off which promises.

The rules pinned here were each paid for in a mistake. Over-include rather than
drop, because a promise nothing can filter must still be examined. Do not
narrow by book, because a filter that misses a cross-book payoff does not
merely miss it — combined with deriving scope from what survives the pass, it
relabels that promise a series-level debt the continuation has to discharge.
"""

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

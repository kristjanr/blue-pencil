"""The spend cap stops extraction between passes, and stops it *before* it spends.

A submitted batch cannot be un-billed, so the only useful enforcement point is
ahead of the submit. These tests pin both halves: the pre-flight estimate refuses
a pass it cannot afford, and the post-pass check refuses the next one once the
tally has already gone past the cap.
"""

import pytest

from bp.extract import PASSES, _estimate_pass_usd, _schema_for, extract
from bp.policy import RunPolicy


class _Counter:
    """Just enough client to answer count_tokens; any submit is a test failure."""

    def __init__(self, per_call=4_000):
        self.per_call = per_call
        self.counted = 0

    # --- client.messages.count_tokens(...) ---
    @property
    def messages(self):
        return self

    def count_tokens(self, **_kw):
        self.counted += 1
        return type("R", (), {"input_tokens": self.per_call})()

    # --- anything that would actually spend money ---
    @property
    def batches(self):
        raise AssertionError("a capped run must not reach the Batch API")


def test_cap_refuses_the_first_pass_it_cannot_afford(world):
    graph, profile = world
    client = _Counter()
    report = extract(graph, profile, RunPolicy.from_dict({}), client,
                     use_batch=True, max_usd=0.0001)
    assert report.stopped, "a cap of a hundredth of a cent must stop the run"
    assert "entities" in report.stopped  # stopped at the first pass, before submitting
    assert report.passes_done == []
    assert report.usage.usd == 0.0
    assert client.counted > 0, "the estimate must be measured, not assumed"
    assert "STOPPED EARLY" in report.render()


def test_no_cap_means_no_estimate_round_trip(world):
    """Passing no cap must not add a count_tokens call per pass."""
    graph, profile = world
    client = _Counter()
    with pytest.raises(AssertionError, match="Batch API"):
        extract(graph, profile, RunPolicy.from_dict({}), client, use_batch=True, max_usd=None)
    assert client.counted == 0


def test_estimate_scales_with_scene_count_and_is_halved_by_batch(world):
    graph, profile = world
    scenes = graph.scenes()
    assert scenes, "the synthetic world must have scenes to price"
    schema = _schema_for("entities")
    args = (_Counter(), "claude-sonnet-5", profile, "entities", schema)

    sync = _estimate_pass_usd(*args, scenes, batch=False)
    batched = _estimate_pass_usd(*args, scenes, batch=True)
    half = _estimate_pass_usd(*args, scenes[: max(1, len(scenes) // 2)], batch=False)

    assert sync > 0
    assert batched == pytest.approx(sync / 2)
    assert half == pytest.approx(sync / 2, rel=0.6)  # roughly linear in scenes


def test_estimate_survives_a_client_that_cannot_count(world):
    """No count_tokens (offline, stub) must fall back, never price a pass at zero."""
    graph, profile = world

    class _Broken(_Counter):
        def count_tokens(self, **_kw):
            raise RuntimeError("no network")

    usd = _estimate_pass_usd(_Broken(), "claude-sonnet-5", profile, "entities",
                             _schema_for("entities"), graph.scenes(), batch=True)
    assert usd > 0


def test_every_pass_can_be_priced(world):
    """A schema that cannot be priced would silently disable the cap for that pass."""
    graph, profile = world
    for name in PASSES:
        usd = _estimate_pass_usd(_Counter(), "claude-sonnet-5", profile, name,
                                 _schema_for(name), graph.scenes(), batch=True)
        assert usd > 0, f"pass {name} priced at zero"


def test_custom_ids_are_wire_legal_and_unique(world):
    """The Batch API allows [a-zA-Z0-9_-] only; scene IDs carry a space."""
    import re

    from bp.extract import _custom_id

    graph, _ = world
    ids = [s.scene_id for s in graph.scenes()]
    assert ids
    slugs = [_custom_id("entities", i) for i in ids]
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", s) for s in slugs)
    assert len(set(slugs)) == len(slugs)
    # The real corpus shape, which is what broke the first submit.
    assert _custom_id("entities", "book 1.01.1") == "entities--book_1_01_1"

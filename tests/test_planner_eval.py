"""Planning shape and evaluation scoring — the deterministic halves."""

import pytest

from bp.evalharness import (
    ConstraintReport, ProbeResult, _js_divergence, plot_recall, tier_a,
)
from bp.models import EndingHypothesis, Move
from bp.planner import Skeleton, measure_skeleton, shortlist


def test_skeleton_is_measured_from_the_corpus(world):
    graph, _ = world
    s = measure_skeleton(graph)
    assert s.chapters > 0
    assert set(s.pov_slate) >= {"Ana", "Boro", "Cyra"}
    assert abs(sum(s.pov_share.values()) - 1.0) < 1e-9
    assert s.mean_chapter_words > 0


def test_move_score_rewards_futures_and_looks_ahead():
    dull = Move(summary="A and B agree to cooperate", setup=0.5, cost=0.1,
                character_truth=0.9, inevitable_in_hindsight=0.3, futures_opened=0.1)
    live = Move(summary="A learns B's secret; B realises A knows; both pretend otherwise",
                setup=0.5, cost=0.1, character_truth=0.9, inevitable_in_hindsight=0.3,
                futures_opened=0.9)
    assert live.score > dull.score

    leads_nowhere = Move(summary="x", setup=1, cost=1, character_truth=1,
                         inevitable_in_hindsight=1, futures_opened=1,
                         children=[Move(summary="dead end", futures_opened=0.0)])
    assert leads_nowhere.score < 1.0     # a good scene that leads nowhere is discounted


def test_shortlist_keeps_the_scores_with_the_moves():
    moves = [Move(summary=str(i), futures_opened=i / 10) for i in range(10)]
    top = shortlist(moves, n=3)
    assert len(top) == 3 and top[0].futures_opened > top[-1].futures_opened


def test_ending_hypothesis_penalises_orphaned_promises():
    weights = {"P1": 1.0, "P2": 1.0, "P3": 1.0}
    pays = EndingHypothesis(hypothesis_id="a", summary="x", pays=["P1", "P2", "P3"])
    orphans = EndingHypothesis(hypothesis_id="b", summary="y", pays=["P1"], orphans=["P2", "P3"])
    assert pays.evidence_score(weights) > orphans.evidence_score(weights)


def test_graph_evidence_sums_the_weight_of_paid_promises_not_their_count():
    """The bug this test exists for: paid/orphaned are ids, and dividing how
    many of them there are by a *summed* weight collapses coverage toward
    zero regardless of how good the hypothesis is, because a count and a
    weight sum are not the same unit. A hypothesis that pays the two heaviest
    promises in a lopsided ledger must show much higher coverage than one
    that pays the same *number* of negligible ones."""
    weights = {"heavy1": 0.9, "heavy2": 0.9, "light1": 0.01, "light2": 0.01, "light3": 0.01}
    pays_heavy = EndingHypothesis(hypothesis_id="a", summary="x", pays=["heavy1", "heavy2"])
    pays_light = EndingHypothesis(hypothesis_id="b", summary="y", pays=["light1", "light2"])
    assert pays_heavy.graph_evidence(weights) > 0.9
    assert pays_light.graph_evidence(weights) < 0.02


def test_graph_evidence_ignores_promise_ids_the_model_invented():
    """A promise id absent from the real ledger must not count toward
    coverage — otherwise a hypothesis can inflate its score just by claiming
    to pay off ids that were never offered to it."""
    weights = {"real": 1.0}
    h = EndingHypothesis(hypothesis_id="a", summary="x", pays=["real", "invented"])
    assert h.graph_evidence(weights) == pytest.approx(1.0)


def test_evidence_score_reports_the_self_reported_half_separately():
    """graph_evidence is the part the graph verified; evidence_score also
    blends in the model's own opinion of itself. Keeping them separable is
    the point — a low-evidence hypothesis should not be able to hide behind
    a confident self-report."""
    weights = {"P1": 1.0}
    h = EndingHypothesis(hypothesis_id="a", summary="x", pays=[],
                         fits_author_statements=1.0, structural_symmetry=1.0)
    assert h.graph_evidence(weights) == 0.0
    assert h.evidence_score(weights) == pytest.approx(0.4)


def test_js_divergence_is_zero_for_identical_distributions():
    d = {"A": 0.6, "B": 0.4}
    assert _js_divergence(d, dict(d)) < 1e-9
    assert _js_divergence(d, {"A": 0.1, "B": 0.9}) > 0.1


def test_tier_a_compares_shape_not_plot():
    real = Skeleton(chapters=40, mean_chapter_words=3000, sd_chapter_words=700,
                    pov_slate=["A", "B"], pov_share={"A": 0.5, "B": 0.5},
                    parallel_threads=6, turning_point_cadence=4.0)
    close = Skeleton(chapters=42, mean_chapter_words=3100, sd_chapter_words=750,
                     pov_slate=["A", "B"], pov_share={"A": 0.52, "B": 0.48},
                     parallel_threads=6, turning_point_cadence=4.1)
    far = Skeleton(chapters=12, mean_chapter_words=9000, sd_chapter_words=100,
                   pov_slate=["A"], pov_share={"A": 1.0}, parallel_threads=1,
                   turning_point_cadence=12.0)
    assert tier_a(close, real).pov_divergence < tier_a(far, real).pov_divergence
    assert "6/6" in tier_a(close, real).verdict


def test_plot_recall_excludes_what_the_bare_model_already_knew():
    """The contamination control. Credit is only for what memory could not supply."""
    real = ["The Vela relay is destroyed by the Quiet",
            "Boro seals the Kepler vault against the tide"]
    generated = list(real)
    probe = ProbeResult(book="LW4", knows=True, confidence=0.9,
                        recalled_events=["The Vela relay is destroyed by the Quiet"])

    naive = plot_recall(real, generated, None)
    adjusted = plot_recall(real, generated, probe)
    assert naive.credited == 2                  # what a naive benchmark would report
    assert adjusted.credited == 1               # what is actually evidence
    assert len(adjusted.matched_but_known) == 1


def test_probe_blindness_is_the_admission_ticket():
    # The shape a real probe returns: a confident denial with empty hands.
    assert ProbeResult(book="x", knows=False, confidence=0.9).blind
    # Claims to know it — not blind, and no need to look further.
    assert not ProbeResult(book="x", knows=True, confidence=0.9).blind
    # Denies knowing it and recalls events anyway. The events are the evidence;
    # the denial is not.
    assert not ProbeResult(book="x", knows=False, confidence=0.9,
                           recalled_events=["the station falls"]).blind
    # A denial the model is itself unsure of is uncertainty, not blindness.
    assert not ProbeResult(book="x", knows=False, confidence=0.05).blind


def test_tier_b_needs_no_answer_key(world):
    from bp.draftdoc import Draft
    from bp.evalharness import tier_b
    from bp.policy import RunPolicy
    from pathlib import Path

    graph, profile = world
    chapters = Path(__file__).parent / "fixtures" / "chapters"
    drafts = [Draft.load(chapters / "clean.md"), Draft.load(chapters / "planted.md")]
    report = tier_b(graph, profile, RunPolicy.from_dict({}), drafts)
    assert report.chapters == 2 and report.words > 0
    assert report.hard > 0 and report.violations_per_10k > 0
    assert "epistemic" in report.by_check


def test_ablation_switches_one_thing_off():
    from bp.evalharness import ablation_policy
    from bp.policy import RunPolicy

    base = RunPolicy.from_dict({})
    ablated = ablation_policy(base, "no_belief_graph")
    assert ablated.check("epistemic").severity == "off"
    assert base.check("epistemic").severity == "hard"       # base untouched
    assert ablation_policy(base, "half_context").context_start_tokens == base.context_start_tokens // 2


def test_propose_thesis_retries_when_the_model_shorts_the_requested_count(monkeypatch, world):
    """The bug this test exists for: `n` in the prompt is only a request, and
    a model asked for 2 hypotheses has returned 1. propose_thesis must notice
    the shortfall and ask again for exactly what's missing, rather than
    silently handing back fewer hypotheses than the caller asked for."""
    from bp import planner
    from bp.policy import RunPolicy

    graph, profile = world
    prompts: list[str] = []

    def fake_structured(client, model, schema, *, system, prompt, max_tokens, usage=None, stage=""):
        prompts.append(prompt)
        if len(prompts) == 1:
            items = [EndingHypothesis(hypothesis_id="h1", summary="only one")]
        else:
            items = [EndingHypothesis(hypothesis_id="h2", summary="the missing one")]
        return schema(items=items)

    monkeypatch.setattr(planner, "structured", fake_structured)
    result = planner.propose_thesis(graph, profile, RunPolicy(), client=object(), n=2)

    assert len(result) == 2
    assert len(prompts) == 2
    assert "Propose 2 " in prompts[0]
    assert "exactly 1" in prompts[1]


def test_propose_thesis_does_not_retry_when_the_count_is_met(monkeypatch, world):
    from bp import planner
    from bp.policy import RunPolicy

    graph, profile = world
    calls = 0

    def fake_structured(client, model, schema, *, system, prompt, max_tokens, usage=None, stage=""):
        nonlocal calls
        calls += 1
        return schema(items=[EndingHypothesis(hypothesis_id=f"h{i}", summary="x") for i in range(2)])

    monkeypatch.setattr(planner, "structured", fake_structured)
    result = planner.propose_thesis(graph, profile, RunPolicy(), client=object(), n=2)

    assert len(result) == 2
    assert calls == 1

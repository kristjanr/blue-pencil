"""Planning shape and evaluation scoring — the deterministic halves."""

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
    pays = EndingHypothesis(hypothesis_id="a", summary="x", pays=["P1", "P2", "P3"])
    orphans = EndingHypothesis(hypothesis_id="b", summary="y", pays=["P1"], orphans=["P2", "P3"])
    assert pays.evidence_score(3) > orphans.evidence_score(3)


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
    assert ProbeResult(book="x", knows=False, confidence=0.05).blind
    assert not ProbeResult(book="x", knows=True, confidence=0.9).blind
    assert not ProbeResult(book="x", knows=False, confidence=0.6).blind


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

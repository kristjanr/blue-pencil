"""The Phase 2 exit test: errors planted in a chapter someone else wrote.

The plan gates the whole project on this. If the graph cannot catch a planted
error in a chapter the engine did not write, there is no point drafting one —
so the Continuity Editor ships before any generator exists, and this is the test
that says whether it works.

Two numbers matter, and they pull against each other:

* **recall** — how many planted errors are caught. Target: 18 of 20.
* **false alarms** — hard findings on a chapter with nothing wrong. Target: at
  most 2. This is the number that actually decides whether the tool gets used.
  An editor forgives a missed error; they stop reading a tool that cries wolf.

Every planted error below is a *specific* violation with a known correct answer,
and the expected checker is named. A finding of the right kind in the wrong place
does not count.
"""

from pathlib import Path

import pytest

from bp.checks import CheckContext, run_checks
from bp.draftdoc import Draft
from bp.models import ChapterCard
from bp.policy import RunPolicy

CHAPTERS = Path(__file__).parent / "fixtures" / "chapters"

#: (label, expected check, expected line window). Twenty planted errors.
PLANTED = [
    # --- information paths: seven references nobody on the page could make ---
    ("Vela relay destruction, 12 ly away, still 7 years in transit", "epistemic", (9, 22)),
    ("Kepler vault sealing, 6 ly away, still 2 years in transit", "epistemic", (12, 22)),
    ("second cradle found empty, 6 ly away, 5 years in transit", "epistemic", (12, 22)),
    ("Cyra's drift-lane survey, no path to Sol yet", "epistemic", (15, 25)),
    ("Thule going silent: no observer, no report, no path at all", "epistemic", (30, 40)),
    ("Dael's death: nobody survived to send it", "epistemic", (30, 40)),
    ("the lattice coming online — an event five years in this chapter's future", "epistemic", (38, 46)),
    # --- geography: two bodies that could not be in the room ---
    ("Boro at Sol, 6 ly and 3 years' travel from where he was", "geography", (1, 20)),
    ("Cyra at Sol, 12 ly and 6 years' travel from where she was", "geography", (1, 20)),
    # --- registry: a dead man and a sealed object ---
    ("the Ledger Stone on the table, sealed in a vault 6 ly away", "objects", (28, 36)),
    ("Dael acting on the page while recorded dead", "objects", (33, 42)),
    # --- the card contract ---
    ("carded reveal absent: the archive editing Ana's charts", "card", None),
    ("carded reveal absent: the courier drone's sealed instruction", "card", None),
    ("354 words against a 3,000-word budget", "card", None),
    ("carded consequence never shown", "consequence", None),
    # --- repetition, against this POV's measured canon baseline ---
    ("'the traffic lanes were' three times in one paragraph", "repetition", None),
    ("'answer either way' hammered four times", "repetition", None),
    # --- voice drift against the technique spec ---
    ("dialogue ratio far above this POV's canon", "voice", None),
    ("sensory density above this POV's canon", "voice", None),
    ("lexical variety above this POV's canon", "voice", None),
]


#: Longwater is built, not ingested: every event, report and observer in it is
#: recorded on purpose, so a missing information path really is a missing one.
#: Saying so is what lets "nobody survived to send it" stay a hard finding here
#: while the same branch abstains on a real corpus, where propagation is
#: recorded for only a fraction of events.
COMPLETE_WORLD = {"checks": {"epistemic": {"severity": "hard", "paths_are_complete": True}}}


def _scorecard(world, name, card=None, policy=None):
    graph, profile = world
    draft = Draft.load(CHAPTERS / f"{name}.md")
    return draft, run_checks(CheckContext(
        draft=draft, graph=graph, profile=profile,
        policy=policy or RunPolicy.from_dict(COMPLETE_WORLD), card=card,
    ))


@pytest.fixture
def card():
    return ChapterCard.model_validate_json((CHAPTERS / "card.json").read_text())


def test_no_checker_crashes(world, card):
    """A checker that raises is stood down, not silently skipped. Neither should
    happen on a well-formed chapter."""
    _, sc = _scorecard(world, "planted", card)
    assert sc.skipped == {}, f"checkers stood down unexpectedly: {sc.skipped}"


def test_recall_on_planted_errors(world, card):
    _, sc = _scorecard(world, "planted", card)
    found = sc.marginalia
    caught, missed, note_only = [], [], []
    for label, check, window in PLANTED:
        hits = [m for m in found if m.check == check]
        if window is not None:
            lo, hi = window
            hits = [m for m in hits if lo <= m.line <= hi]
        # A note is the checker saying "I could not decide". An error that
        # produces only a note has not been caught in any way a writer would
        # act on -- it arrives in a pile of several hundred. Recall means the
        # chapter was actually stopped. Matching on check-and-line alone let a
        # hard finding silently decay to a note without this gate noticing,
        # which is exactly what it exists to prevent.
        acted = [m for m in hits if m.severity in ("hard", "soft")]
        if acted:
            caught.append(label)
        elif hits:
            note_only.append(label)
            missed.append(f"{label} (note only — decayed, not absent)")
        else:
            missed.append(label)

    assert len(caught) >= 18, (
        f"caught {len(caught)}/20 planted errors"
        + (f", {len(note_only)} of them downgraded to notes" if note_only else "")
        + "; missed:\n  " + "\n  ".join(missed)
    )
    # The recall threshold alone cannot protect against decay: measured, turning
    # `paths_are_complete` off costs exactly 2 findings, and the threshold has
    # exactly 2 of slack, so 18/20 still passes while two errors have quietly
    # stopped stopping the chapter. In a world that declares its paths complete,
    # no planted error may land as a note -- there is nothing left to be unsure
    # about.
    assert not note_only, (
        "planted errors that produced only notes in a path-complete world:\n  "
        + "\n  ".join(note_only)
    )


def test_false_alarms_on_a_clean_chapter(world):
    """The number that decides whether anyone keeps using this."""
    _, sc = _scorecard(world, "clean")
    assert len(sc.hard) == 0, (
        "hard findings on a chapter with nothing wrong:\n  "
        + "\n  ".join(f"{m.check}: {m.message}" for m in sc.hard)
    )
    assert len(sc.hard) + len(sc.soft) <= 4


def test_each_hard_finding_offers_a_fix(world, card):
    """A checker that only says 'this is wrong' makes the human diagnose twice."""
    _, sc = _scorecard(world, "planted", card)
    for m in sc.hard:
        assert m.fixes, f"{m.check} reported a hard failure with no suggested fix"


def test_epistemic_findings_cite_the_conflicting_scene(world, card):
    _, sc = _scorecard(world, "planted", card)
    epistemic = [m for m in sc.marginalia if m.check == "epistemic"]
    assert epistemic
    assert all(m.citations for m in epistemic), "a continuity claim with no citation behind it"


def test_one_mark_per_error_not_one_per_character(world, card):
    """Three marks for one bad line is how a margin becomes unreadable."""
    _, sc = _scorecard(world, "planted", card)
    by_line = {}
    for m in (x for x in sc.marginalia if x.check == "epistemic"):
        by_line.setdefault((m.line, m.message[:40]), []).append(m)
    assert all(len(v) == 1 for v in by_line.values())


def test_clean_chapter_is_gate_clean(world):
    _, sc = _scorecard(world, "clean")
    assert sc.clean, "a clean chapter must pass an auto_if_clean gate"


def test_planted_chapter_is_not_gate_clean(world, card):
    _, sc = _scorecard(world, "planted", card)
    assert not sc.clean


def test_an_untraceable_path_is_a_note_unless_the_graph_is_complete(world, card):
    """The defect this test exists for: `_violation` reported ignorance as a
    violation. On the real book 6 that produced 161 of 168 hard findings, and
    not one of them had computed an arrival date — the check never once showed
    that information could not arrive, it reported 161 times that it could not
    trace how it did.

    Whether absence proves anything is a property of the corpus, so it is
    declared. Default off: a graph that records propagation for a fraction of
    its events cannot conclude anything from silence."""
    from bp.policy import RunPolicy as RP

    _, complete = _scorecard(world, "planted", card)
    _, sparse = _scorecard(world, "planted", card, policy=RP.from_dict({}))

    def untraceable(sc):
        return [m for m in sc.marginalia
                if m.check == "epistemic" and m.metric.get("earliest_arrival_day") == -1.0]

    assert untraceable(complete), "the fixture plants paths that genuinely do not exist"
    assert all(m.severity == "hard" for m in untraceable(complete)), \
        "in a graph that records every report, no path really is no path"
    assert all(m.severity == "note" for m in untraceable(sparse)), \
        "without that guarantee, not-found is the checker abstaining"

    # The computed branch is unaffected either way — it proved its finding.
    computed = [m for m in sparse.marginalia
                if m.check == "epistemic" and m.metric.get("earliest_arrival_day", -1.0) > 0]
    assert computed and all(m.severity == "hard" for m in computed)


def test_a_dead_faction_is_not_a_resurrection(world):
    """`the Pav is recorded dead but acts in this chapter` — the Pav being a
    species, not somebody who could be restored from backup. Only a character
    can be wrongly un-dead; a disbanded faction reappearing is a different
    question, and the checker has no business calling it a hard finding."""
    from bp.checks.objects import ObjectAndBodyCheck
    from bp.models import Citation, Entity

    graph, profile = world
    cit = [Citation(scene=graph.scenes()[0].scene_id, quote="q")]
    graph.write_entity(Entity(entity_id="the_pav", name="Pavlov", kind="faction",
                              status="dead", citations=cit))
    graph.write_entity(Entity(entity_id="dead_captain", name="Kessring", kind="character",
                              status="dead", citations=cit))
    graph.commit()

    draft = Draft.parse("Pavlov attacked the convoy. Kessring attacked the convoy too.\n")
    draft.meta = {"pov": "Ana", "date": "2185-01-01", "place": "Sol"}
    ctx = CheckContext(draft=draft, graph=graph, profile=profile,
                       policy=RunPolicy.from_dict({}))
    named = {m.message.split()[0] for m in ObjectAndBodyCheck().run(ctx)}

    assert "Kessring" in named, "a dead character acting on the page is the real finding"
    assert "Pavlov" not in named, "a faction is not somebody who can be resurrected"

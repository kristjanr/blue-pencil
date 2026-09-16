"""Every record should be able to prove itself against the scene it names."""

from bp.grounding import _norm, _terms, check_grounding
from bp.models import Citation, Entity, Event


def test_possessives_and_hyphens_do_not_become_phantom_terms():
    """`Hal's` must look for `hal`, or the check invents failures."""
    t = _terms("Hal's SUDDAR found Bob-1 near Delta-4")
    assert "hal" in t and "hal's" not in t
    assert not any(x.endswith(("-", "'")) for x in t)


def test_typographic_differences_are_not_real_differences():
    """Curly quotes, em dashes and ellipses fold the same way on both sides."""
    assert _norm("“Don’t—stop…”") == _norm('"Don\'t-stop..."')
    assert _norm("  a   b\n c ") == "a b c"


def test_a_pov_character_is_not_required_in_their_own_scene(world):
    """First person: Bill's chapter says "I", never "Bill"."""
    graph, _ = world
    scene = graph.scenes()[0]
    graph.conn.execute("UPDATE scenes SET pov=?, text=? WHERE scene_id=?",
                       ("Zarquon", "I walked to the ridge and waited.", scene.scene_id))
    graph.write_entity(Entity(
        entity_id="E-pov", name="Zarquon walked to the ridge", kind="character",
        citations=[Citation(scene=scene.scene_id, quote="I walked to the ridge")]))
    graph.commit()
    rep = check_grounding(graph)
    assert not any(f.record_id == "E-pov" for f in rep.findings), \
        "the narrator's own name missing from their own scene is not a defect"


def test_a_quote_that_is_not_in_its_scene_is_reported(world):
    graph, _ = world
    scene = graph.scenes()[0]
    graph.write_event(Event(
        event_id="E-fake", summary="Something happened", when="2186-01-01", where="Sol",
        observed_by=["Ana"],
        citations=[Citation(scene=scene.scene_id,
                            quote="a sentence that appears nowhere in this corpus at all")]))
    graph.commit()
    rep = check_grounding(graph)
    hit = next((f for f in rep.findings if f.record_id == "E-fake"), None)
    assert hit is not None and not hit.quote_ok
    assert hit.severity == "quote not in scene"


def test_a_real_quote_passes(world):
    graph, _ = world
    scene = graph.scenes()[0]
    snippet = " ".join(scene.text.split()[:8])
    graph.write_event(Event(
        event_id="E-real", summary="A thing", when="2186-01-01", where="Sol",
        observed_by=["Ana"], citations=[Citation(scene=scene.scene_id, quote=snippet)]))
    graph.commit()
    rep = check_grounding(graph)
    assert not any(f.record_id == "E-real" and not f.quote_ok for f in rep.findings)


def test_demotion_strips_authority_but_keeps_the_record(world):
    """An unprovable record may still be true; what it cannot do is prove itself."""
    from bp.grounding import demote_unproven

    graph, _ = world
    scene = graph.scenes()[0].scene_id
    graph.write_event(Event(
        event_id="E-unproven", summary="Asserted without evidence",
        when="2186-01-01", where="Sol", observed_by=["Ana"],
        claim_type="explicit", confidence=0.95,
        citations=[Citation(scene=scene, quote="a line that is nowhere in this corpus")]))
    graph.write_event(Event(
        event_id="E-proven", summary="Backed by the page",
        when="2186-01-01", where="Sol", observed_by=["Ana"],
        claim_type="explicit", confidence=0.95,
        citations=[Citation(scene=scene,
                            quote=" ".join(graph.scenes()[0].text.split()[:8]))]))
    graph.commit()

    counts = demote_unproven(graph, check_grounding(graph))
    assert counts.get("event", 0) >= 1

    row = graph.conn.execute(
        "SELECT claim_type, confidence FROM events WHERE event_id='E-unproven'").fetchone()
    assert row["claim_type"] == "inferred"
    assert row["confidence"] <= 0.5
    # still there — deletion would lose a claim that is probably true
    assert graph.conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_id='E-unproven'").fetchone()[0] == 1

    keep = graph.conn.execute(
        "SELECT claim_type, confidence FROM events WHERE event_id='E-proven'").fetchone()
    assert keep["claim_type"] == "explicit" and keep["confidence"] == 0.95


def test_demotion_is_idempotent(world):
    from bp.grounding import demote_unproven

    graph, _ = world
    graph.write_event(Event(
        event_id="E-twice", summary="Unprovable", when="2186-01-01", where="Sol",
        observed_by=["Ana"], claim_type="explicit", confidence=0.9,
        citations=[Citation(scene=graph.scenes()[0].scene_id, quote="not in the corpus at all")]))
    graph.commit()
    first = demote_unproven(graph, check_grounding(graph))
    second = demote_unproven(graph, check_grounding(graph))
    assert first.get("event", 0) >= 1
    assert second.get("event", 0) == 0, "a second run must be a no-op"


def test_punctuation_is_not_evidence_of_invention(world):
    """The bug this test exists for: 156 of 391 "quotes that appear nowhere in
    the corpus" were punctuation artefacts — a curly quotation mark placed at
    the wrong end of the line, an interjection whose quote marks the model
    dropped. Each one demoted a record that could prove itself perfectly well,
    and the 391 was a number quoted to the project's owner repeatedly."""
    graph, _ = world
    scene = graph.scenes()[0]
    real = " ".join(scene.text.split()[:14])

    graph.write_event(Event(
        event_id="E-punct", summary="quoted with the marks moved", when="2186-01-01",
        where="Sol", observed_by=["Ana"],
        citations=[Citation(scene=scene.scene_id, quote=f'"{real}')]))
    graph.write_event(Event(
        event_id="E-invented", summary="not in the text at all", when="2186-01-01",
        where="Sol", observed_by=["Ana"],
        citations=[Citation(scene=scene.scene_id,
                            quote="a sentence that appears nowhere in this corpus whatsoever")]))
    graph.commit()

    rep = check_grounding(graph)
    bad = {f.record_id for f in rep.findings if not f.quote_ok}
    assert "E-punct" not in bad, "a stray quotation mark is not a fabricated quote"
    assert "E-invented" in bad, "but an invented one must still be caught"


def test_the_rescue_needs_real_overlap_not_a_coincidence(world):
    """The loose comparison only ever rescues; a short or unrelated string must
    not slip through it."""
    from bp.grounding import _loose

    graph, _ = world
    scene = graph.scenes()[0]
    graph.write_event(Event(
        event_id="E-short", summary="too little to match on", when="2186-01-01",
        where="Sol", observed_by=["Ana"],
        citations=[Citation(scene=scene.scene_id, quote="the and a")]))
    graph.commit()

    rep = check_grounding(graph)
    assert "E-short" in {f.record_id for f in rep.findings if not f.quote_ok}
    assert len(_loose("the and a")) <= 30


def test_demotion_records_what_it_overwrote(world):
    """The reason this exists: when 156 demotions turned out to be wrong, the
    only thing that made them reversible was a backup that happened to predate
    them. Luck standing in for design. A restore should be a join."""
    from bp.grounding import demote_unproven

    graph, _ = world
    scene = graph.scenes()[0].scene_id
    graph.write_event(Event(
        event_id="E-trail", summary="asserted without evidence", when="2186-01-01",
        where="Sol", observed_by=["Ana"], claim_type="explicit", confidence=0.95,
        citations=[Citation(scene=scene, quote="a line that is nowhere in this corpus")]))
    graph.commit()

    demote_unproven(graph, check_grounding(graph), run_id="run-under-test")

    changes = {c["field"]: c for c in graph.changes_for("events", "E-trail")}
    assert changes["claim_type"]["old_value"] == "explicit"
    assert changes["claim_type"]["new_value"] == "inferred"
    assert float(changes["confidence"]["old_value"]) == 0.95
    assert all(c["run_id"] == "run-under-test" for c in changes.values())
    assert len(graph.changes_in_run("run-under-test")) >= 2


def test_demotion_records_nothing_when_it_changes_nothing(world):
    """A trail of no-ops is noise, and the second run of a demotion is all
    no-ops."""
    from bp.grounding import demote_unproven

    graph, _ = world
    scene = graph.scenes()[0].scene_id
    graph.write_event(Event(
        event_id="E-twice-trail", summary="unprovable", when="2186-01-01", where="Sol",
        observed_by=["Ana"], claim_type="explicit", confidence=0.9,
        citations=[Citation(scene=scene, quote="not in the corpus at all")]))
    graph.commit()

    demote_unproven(graph, check_grounding(graph), run_id="first")
    demote_unproven(graph, check_grounding(graph), run_id="second")
    assert graph.changes_in_run("first")
    assert graph.changes_in_run("second") == []


# --------------------------------------------------------------- quote fidelity
#: The real line from the corpus, curly apostrophe and all.
_SCENE = ("Riker leaned back. “I can’t forget the feeling of being "
          "controlled. It was like being able to feel a tapeworm moving around "
          "inside your skull.” He shuddered.")


def _verify(quote: str) -> bool:
    from bp.grounding import _norm, quote_in_scene

    return quote_in_scene(quote, _norm(_SCENE), _SCENE)


def test_a_quote_that_is_simply_there_verifies():
    assert _verify("I can’t forget the feeling of being controlled.")


def test_a_doubled_unicode_escape_is_not_a_fabrication():
    """The bug that rejected 27% of the settler's proposed closes.

    Model output carries the escape through literally, and a second JSON hop
    doubles the backslash. The old decoder matched the *second* backslash,
    consumed the escape and left the first one standing — turning a faithful
    quote into one that matched nothing.
    """
    assert _verify("I can\\\\u2019t forget the feeling of being controlled.")
    assert _verify("I can\\u2019t forget the feeling of being controlled.")


def test_a_stray_backslash_mid_word_is_rescued_not_accused():
    """`we\\were` in the pilot: a lost space, not an invented quote."""
    assert _verify("I can\\\\u2019t forget the feeling of being controlled. It was "
                   "like being able to feel a\\tapeworm moving around inside your skull.")


def test_an_invented_quote_still_fails():
    """The guard has to keep working, or loosening it is just switching it off."""
    assert not _verify("I have never felt more in control of my own mind.")
    assert not _verify("")


def test_a_short_quote_is_not_rescued_by_coincidence():
    """Below the floor, loose matching would accept near-anything."""
    assert not _verify("I’t f")

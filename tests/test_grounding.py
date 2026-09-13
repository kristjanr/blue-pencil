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

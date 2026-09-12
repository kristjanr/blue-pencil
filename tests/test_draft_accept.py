"""Context packs and state commit — the parts that work without a model."""

from bp.accept import accept_chapter, state_before
from bp.draft import bible_digest, build_pack, knowledge_block, style_block
from bp.draftdoc import Draft
from bp.models import ChapterCard
from bp.policy import RunPolicy


def _card():
    return ChapterCard(
        card_id="LW4.ch01", book="LW4", chapter=1, pov="Ana", date_inworld="2185-01-01",
        location="Sol", cast=["Ana"], goal="file an honest chart", turn="she signs it dark",
        reveals=[{"what": "the archive has been editing her charts", "to_whom": "Ana"}],
        word_budget=3000, scenes=[{"beats": "Ana works alone", "word_budget": 3000}],
    )


def test_knowledge_block_states_what_the_pov_must_not_act_on(world):
    """This block is why the drafter does not need the books."""
    graph, profile = world
    day = profile.calendar.parse("2185-01-01").lo
    block = knowledge_block(graph, profile, "Ana", day)
    assert "does NOT yet know" in block
    assert "Vela relay" in block           # 7 years out in transit at this date
    later = knowledge_block(graph, profile, "Ana", profile.calendar.parse("2195-01-01").lo)
    assert "knows:" in later


def test_pack_orders_stable_material_first_for_caching(world):
    graph, profile = world
    pack = build_pack(graph, profile, RunPolicy.from_dict({}), _card())
    blocks = pack.system_blocks()
    assert blocks[: len(pack.stable)] == pack.stable
    assert "engine" in pack.rules.lower() or "hard rules" in pack.rules.lower()
    assert pack.card_block.startswith("CHAPTER CARD")


def test_pack_respects_its_token_budget(world):
    graph, profile = world
    policy = RunPolicy.from_dict({"context": {"start_tokens": 4000, "max_tokens": 8000}})
    pack = build_pack(graph, profile, policy, _card())
    assert pack.tokens <= 4000 * 1.15          # trimming is coarse but bounded


def test_trimming_never_sacrifices_the_knowledge_block(world):
    """A pack that drops what a character knows to fit more prose has defeated
    its own purpose."""
    graph, profile = world
    policy = RunPolicy.from_dict({"context": {"start_tokens": 1500, "max_tokens": 2000}})
    pack = build_pack(graph, profile, policy, _card())
    assert "POV STATE" in pack.state_block
    assert pack.card_block.startswith("CHAPTER CARD")


def test_bible_digest_surfaces_unresolved_contradictions(world):
    from bp.models import Contradiction

    graph, profile = world
    graph.write_contradiction(Contradiction(
        contradiction_id="C-1", subject="who opened the vault",
        reading_a="Boro did", reading_b="Boro says he did"))
    digest = bible_digest(graph, profile)
    assert "do not settle these by accident" in digest
    assert "who opened the vault" in digest


def test_style_block_carries_both_layers(world):
    graph, _ = world
    block = style_block(graph.style("Ana"), "Ana")
    assert "measured" in block and "sentence length" in block


def test_accept_moves_the_world(world, tmp_path):
    """Acceptance writes the card's reveals into the belief graph, so the next
    chapter is checked against a world that has advanced."""
    graph, profile = world
    card = _card()
    draft = Draft.parse("Ana filed the chart and signed it dark.\n")
    draft.meta = {"pov": "Ana", "date": "2185-01-01", "place": "Sol", "cast": ["Ana"]}

    before = len(graph.events())
    result = accept_chapter(graph, profile, RunPolicy.from_dict({}), draft, card=card,
                            book="LW4", accepted_dir=tmp_path, git=False)

    assert result.scene_ids and result.card_applied
    assert len(graph.events()) > before
    # The accepted chapter is canon now: retrievable, and citable.
    scene = graph.scene(result.scene_ids[0])
    assert scene is not None and scene.generated
    assert graph.citations_for("event", "LW4.ch01-R1")


def test_accepted_reveal_is_known_by_its_recipient(world, tmp_path):
    from bp.knowledge import KnowledgeGraph

    graph, profile = world
    draft = Draft.parse("Ana filed the chart.\n")
    draft.meta = {"pov": "Ana", "date": "2185-01-01", "place": "Sol", "cast": ["Ana"]}
    accept_chapter(graph, profile, RunPolicy.from_dict({}), draft, card=_card(),
                   book="LW4", accepted_dir=tmp_path, git=False)
    k = KnowledgeGraph(graph).earliest_knowledge("LW4.ch01-R1", "Ana")
    assert k.reachable


def test_state_before_is_a_checkout_not_a_guess(world):
    graph, profile = world
    state = state_before(graph, profile, "Ana", "2185-01-01")
    assert state["character"] == "Ana"
    assert state["beliefs"]["E-001"]["state"] == "unaware"
    later = state_before(graph, profile, "Ana", "2195-01-01")
    assert later["beliefs"]["E-001"]["state"] == "knows"


def test_rollback_undoes_an_acceptance(world, tmp_path):
    from bp.accept import rollback

    graph, profile = world
    draft = Draft.parse("Ana filed the chart.\n")
    draft.meta = {"pov": "Ana", "date": "2185-01-01", "place": "Sol"}
    result = accept_chapter(graph, profile, RunPolicy.from_dict({}), draft,
                            book="LW4", accepted_dir=tmp_path, git=False)
    rollback(graph, result.scene_ids)
    assert graph.scene(result.scene_ids[0]) is None

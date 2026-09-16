"""Ingestion is deterministic and re-runnable, and needs no API key."""

from bp.db import Graph
from bp.ingest import harvest_names, ingest, split_scenes


def test_ingest_end_to_end(tmp_path):
    import synthetic

    corpus = synthetic.write_corpus(tmp_path / "corpus")
    profile = synthetic.make_profile()
    graph = Graph(tmp_path / "g.sqlite", profile, create=True)
    report = ingest(corpus, graph, profile)

    assert report.books == 3 and report.scenes >= 12
    assert report.words > 1000
    assert set(report.povs) >= {"Ana", "Boro", "Cyra", "Dael"}
    # Dates were parsed, so the epistemic arithmetic has something to work with.
    assert report.undated == 0
    graph.close()


def test_ingest_is_idempotent(tmp_path):
    import synthetic

    corpus = synthetic.write_corpus(tmp_path / "corpus")
    profile = synthetic.make_profile()
    graph = Graph(tmp_path / "g.sqlite", profile, create=True)
    first = ingest(corpus, graph, profile)
    second = ingest(corpus, graph, profile)
    assert first.scenes == second.scenes
    assert graph.counts()["scenes"] == first.scenes
    graph.close()


def test_ingest_builds_retrieval_indexes(tmp_path):
    import synthetic
    from bp.retrieval import Retriever

    corpus = synthetic.write_corpus(tmp_path / "corpus")
    profile = synthetic.make_profile()
    graph = Graph(tmp_path / "g.sqlite", profile, create=True)
    ingest(corpus, graph, profile)

    hits = Retriever(graph).search("vault sealed cradle")
    assert hits and any(h.pov == "Boro" for h in hits)
    assert graph.counts()["chunks"] > 0
    graph.close()


def test_scene_splitting_respects_paragraph_boundaries():
    body = "\n\n".join(f"Paragraph {i} with several words in it." * 30 for i in range(12))
    pieces = split_scenes(body, max_words=200)
    assert len(pieces) > 1
    assert all(not p.startswith(" ") for p in pieces)
    # No paragraph was cut in half.
    assert sum(p.count("Paragraph") for p in pieces) == body.count("Paragraph")


def test_name_harvest_skips_sentence_openers():
    text = "The ship left. Ana waited. Ana counted. Then Ana left. When Boro arrived, Boro spoke. Boro sat."
    names = harvest_names([text], min_count=3)
    assert "Ana" in names and "Boro" in names
    assert "The" not in names and "Then" not in names and "When" not in names


def _scene_with(graph, scene_id, text, ord_):
    from bp.db import SceneRow
    graph.add_scene(SceneRow(scene_id=scene_id, book_id="b", chapter=1, scene=1, pov="",
                             date_text="", day_lo=None, day_hi=None, place="", cast=[],
                             text=text, words=len(text.split()), tokens=len(text.split()),
                             ord=ord_))


def test_dropcap_repair_splits_only_a_glued_word(tmp_path):
    """The EPUB writes `<span class=dropcap>I</span>was`, and ingest
    concatenates it faithfully. Gluing is correct almost every time — "T"+"he"
    is "The" — and wrong only when the drop-cap letter is a word by itself."""
    from bp.db import Graph
    from bp.ingest import repair_dropcaps

    g = Graph(tmp_path / "d.sqlite", create=True)
    _scene_with(g, "s1", "Iwas reviewing the most recent scans again.", 1)
    _scene_with(g, "s2", "The relay was quiet. He was reviewing scans.", 2)
    g.commit()

    assert repair_dropcaps(g) == 1
    assert g.scene("s1").text.startswith("I was reviewing")
    assert g.scene("s2").text.startswith("The relay")


def test_dropcap_repair_does_not_split_a_new_proper_noun(tmp_path):
    """The bug this test exists for: a rule that checks only "is the glued form
    absent from the corpus" is true of every proper noun a later book
    introduces. It split Alexander into "A lexander" 87 times. The tail must
    ALSO be a word the series actually uses."""
    from bp.db import Graph
    from bp.ingest import repair_dropcaps

    g = Graph(tmp_path / "d.sqlite", create=True)
    _scene_with(g, "s1", "Alexander raised his hand for silence.", 1)
    _scene_with(g, "s2", "Atlantis was gone, and Asimov with it.", 2)
    g.commit()

    assert repair_dropcaps(g) == 0, "'lexander' is not a word this corpus uses"
    assert g.scene("s1").text.startswith("Alexander")
    assert g.scene("s2").text.startswith("Atlantis")


def test_dropcap_repair_only_touches_the_opening_word(tmp_path):
    """`Ian McKellen` mid-scene is correct and every lexical test flags it."""
    from bp.db import Graph
    from bp.ingest import repair_dropcaps

    g = Graph(tmp_path / "d.sqlite", create=True)
    _scene_with(g, "s1", "She said an actor. Ian was his name, an old one.", 1)
    g.commit()

    assert repair_dropcaps(g) == 0
    assert "Ian was" in g.scene("s1").text


def test_dropcap_repair_is_idempotent_and_leaves_a_trail(tmp_path):
    from bp.db import Graph
    from bp.ingest import repair_dropcaps

    g = Graph(tmp_path / "d.sqlite", create=True)
    _scene_with(g, "s1", "Iwas here. He was there too.", 1)
    g.commit()

    assert repair_dropcaps(g, run_id="r1") == 1
    assert repair_dropcaps(g, run_id="r2") == 0, "a second pass must be a no-op"
    trail = g.changes_in_run("r1")
    assert trail and trail[0]["old_value"] == "Iwas" and trail[0]["new_value"] == "I was"

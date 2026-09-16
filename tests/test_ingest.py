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

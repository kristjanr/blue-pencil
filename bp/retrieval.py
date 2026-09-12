"""Getting the right small piece of the corpus, rather than all of it.

The engine's founding arithmetic is that the story does not fit in the context
window and should not be dumped there even when it does. "Having read
everything" is not the same as "knowing what this character currently believes,
and when they could have learned it". So every generation step assembles a small
exact context pack by query.

Exemplar retrieval is the part that carries the voice. It matches on *situation
and technique*, not surface similarity, because the useful exemplar for a
build-scene is another build-scene by the same POV — showing how this POV
handles this kind of moment — not the passage with the most words in common.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .db import Graph
from .embed import cosine, embed, unpack


def _fts_escape(query: str) -> str:
    """FTS5 treats a lot of punctuation as syntax. Quote every bare term."""
    terms = [t for t in re.findall(r"[A-Za-z0-9']+", query) if len(t) > 1]
    return " OR ".join(f'"{t}"' for t in terms)


@dataclass
class Hit:
    scene_id: str
    pov: str
    text: str
    score: float
    why: str = ""


class Retriever:
    def __init__(self, graph: Graph):
        self.g = graph

    # ------------------------------------------------------------------ search
    def search(self, query: str, *, pov: str | None = None, limit: int = 10) -> list[Hit]:
        """Full-text search over scenes, BM25-ranked."""
        expr = _fts_escape(query)
        if not expr:
            return []
        sql = """SELECT f.scene_id, f.pov, s.text, bm25(scenes_fts) AS rank
                 FROM scenes_fts f JOIN scenes s ON s.scene_id = f.scene_id
                 WHERE scenes_fts MATCH ?"""
        args: list[object] = [expr]
        if pov:
            sql += " AND f.pov = ?"
            args.append(pov)
        sql += " ORDER BY rank LIMIT ?"
        args.append(limit)
        try:
            rows = self.g.conn.execute(sql, args).fetchall()
        except Exception:
            return []
        return [Hit(r["scene_id"], r["pov"], r["text"], -float(r["rank"]), "fts") for r in rows]

    def similar(self, text: str, *, pov: str | None = None, limit: int = 8) -> list[Hit]:
        """Vector-ranked chunks. Second-pass ranking, not the primary filter."""
        q = embed(text)
        sql = """SELECT c.chunk_id, c.scene_id, c.text, c.vec, s.pov
                 FROM chunks c JOIN scenes s ON s.scene_id = c.scene_id WHERE 1=1"""
        args: list[object] = []
        if pov:
            sql += " AND s.pov = ?"
            args.append(pov)
        scored: list[Hit] = []
        for row in self.g.conn.execute(sql, args):
            sim = cosine(q, unpack(row["vec"]))
            if sim > 0:
                scored.append(Hit(row["scene_id"], row["pov"], row["text"], sim, "vector"))
        scored.sort(key=lambda h: -h.score)
        return scored[:limit]

    # --------------------------------------------------------------- exemplars
    def exemplars(
        self,
        pov: str,
        *,
        situation: str = "",
        techniques: list[str] | None = None,
        limit: int = 6,
    ) -> list[Hit]:
        """Canon passages showing how this POV handles this kind of moment.

        Scoring is structure first, prose second: a technique tag match is worth
        more than any amount of lexical overlap, because the tag is what says
        *this is the same kind of moment*.
        """
        techniques = [t.lower() for t in (techniques or [])]
        rows = self.g.conn.execute(
            "SELECT * FROM exemplars WHERE pov = ? COLLATE NOCASE", (pov,)
        ).fetchall()
        if not rows:
            # No tagged exemplars yet (extraction hasn't run): fall back to the
            # POV's own scenes, ranked lexically. Worse, but never empty.
            hits = self.similar(situation or pov, pov=pov, limit=limit)
            for h in hits:
                h.why = "untagged fallback"
            return hits

        import json

        q = embed(situation) if situation else []
        scored: list[tuple[float, Hit]] = []
        for r in rows:
            tags = [t.lower() for t in json.loads(r["techniques"] or "[]")]
            tag_hits = len(set(tags) & set(techniques))
            sit = 1.0 if situation and situation.lower() in (r["situation"] or "").lower() else 0.0
            lex = cosine(q, embed(r["excerpt"])) if q else 0.0
            score = 2.0 * tag_hits + 1.5 * sit + lex
            why = ", ".join(filter(None, [
                f"{tag_hits} technique tag(s)" if tag_hits else "",
                "situation match" if sit else "",
            ])) or "lexical only"
            scored.append((score, Hit(r["scene_id"], r["pov"], r["excerpt"], score, why)))
        scored.sort(key=lambda t: -t[0])
        return [h for _, h in scored[:limit]]

    # --------------------------------------------------------------- narrative
    def previous_chapters(self, before_ord: int, *, limit: int = 3) -> list[Hit]:
        rows = self.g.conn.execute(
            "SELECT scene_id, pov, text FROM scenes WHERE ord < ? ORDER BY ord DESC LIMIT ?",
            (before_ord, limit),
        ).fetchall()
        return [Hit(r["scene_id"], r["pov"], r["text"], 0.0, "recency") for r in rows]

    def previous_in_pov(self, pov: str, before_ord: int) -> Hit | None:
        row = self.g.conn.execute(
            "SELECT scene_id, pov, text FROM scenes WHERE pov=? AND ord < ? ORDER BY ord DESC LIMIT 1",
            (pov, before_ord),
        ).fetchone()
        return Hit(row["scene_id"], row["pov"], row["text"], 0.0, "same POV") if row else None

    def phrase_ledger(self, before_ord: int, *, window_words: int = 40_000) -> list[str]:
        """Distinctive phrases from the last N words. Handed to the drafter as
        do-not-reuse, and to the repetition auditor as recent history."""
        rows = self.g.conn.execute(
            "SELECT scene_id, words FROM scenes WHERE ord < ? ORDER BY ord DESC", (before_ord,)
        ).fetchall()
        keep, total = [], 0
        for r in rows:
            keep.append(r["scene_id"])
            total += r["words"] or 0
            if total >= window_words:
                break
        if not keep:
            return []
        marks = ",".join("?" * len(keep))
        out = self.g.conn.execute(
            f"SELECT DISTINCT phrase FROM phrase_ledger WHERE scene_id IN ({marks})", keep
        ).fetchall()
        return [r["phrase"] for r in out]

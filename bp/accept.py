"""Stage 6 — accepting a chapter moves the world.

The accepted chapter is re-extracted by the *same* agents that read canon, and
the deltas are written to the graph with citations pointing at the accepted
chapter. From then on retrieval treats it as canon.

That is what makes chapter sixty consistent with chapter three without either
being in context. The graph is the memory; the chapters are its journal.

Because beliefs are dated, the state of the world immediately before any chapter
is reconstructible: "what did every POV believe going into chapter 47" is a
checkout, not a guess.

Human edits to any graph file are first-class. If you correct a belief by hand,
the engine works from your correction — nothing here overwrites a hand-edited
record without saying so.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .db import Graph, SceneRow
from .draftdoc import Draft
from .extract import ExtractReport, extract
from .ingest import _index_chunks, _index_phrases
from .models import ChapterCard, Citation
from .policy import RunPolicy
from .profile import SeriesProfile
from .textstats import estimate_tokens, words


@dataclass
class AcceptResult:
    scene_ids: list[str] = field(default_factory=list)
    committed: str = ""
    deltas: ExtractReport | None = None
    card_applied: bool = False
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"accepted {len(self.scene_ids)} scene(s): {', '.join(self.scene_ids)}"]
        if self.deltas:
            lines.append("graph deltas — " + self.deltas.render().splitlines()[1])
        if self.card_applied:
            lines.append("card reveals written to the belief graph")
        if self.committed:
            lines.append(f"git commit {self.committed}")
        lines += self.notes
        return "\n".join(lines)


def accept_chapter(
    graph: Graph,
    profile: SeriesProfile,
    policy: RunPolicy,
    draft: Draft,
    *,
    card: ChapterCard | None = None,
    client=None,
    book: str = "",
    chapter: int | None = None,
    accepted_dir: Path | None = None,
    git: bool = True,
) -> AcceptResult:
    result = AcceptResult()
    book = book or (card.book if card else "wip")
    chapter = chapter if chapter is not None else (card.chapter if card else _next_chapter(graph, book))
    pov = profile.canonical(draft.pov or (card.pov if card else ""))
    date_text = draft.date_text or (card.date_inworld if card else "")
    place = draft.place or (card.location if card else "")
    span = None
    if date_text:
        try:
            span = profile.calendar.parse(date_text)
        except Exception:
            result.notes.append(f"could not parse date {date_text!r}; scene stored undated")

    base_ord = (graph.conn.execute("SELECT MAX(ord) AS m FROM scenes").fetchone()["m"] or 0) + 1
    cast = draft.cast or ([pov] if pov else [])

    for i, scene in enumerate(draft.scenes, start=1):
        scene_id = f"{book}.{chapter:02d}.{i}"
        graph.add_scene(SceneRow(
            scene_id=scene_id, book_id=book, chapter=chapter, scene=i, pov=pov,
            date_text=date_text, day_lo=span.lo if span else None, day_hi=span.hi if span else None,
            place=place, cast=cast, text=scene.text, words=len(words(scene.text)),
            tokens=estimate_tokens(scene.text), ord=base_ord + i - 1, generated=True,
            chapter_title=(card.turn if card else ""),
        ))
        _index_chunks(graph, scene_id, scene.text)
        _index_phrases(graph, scene_id, scene.text, base_ord + i - 1)
        result.scene_ids.append(scene_id)

    graph.add_book(book, book, _book_ord(graph, book))

    # The card's reveals are the *authored* deltas: what the plan says changed
    # hands. They go in before re-extraction so extraction's own findings can be
    # compared against them rather than duplicating them.
    if card is not None:
        _apply_card(graph, profile, card, result.scene_ids, span)
        result.card_applied = True

    # Re-extract the generated text exactly as canon was extracted.
    if client is not None:
        new_scenes = [graph.scene(sid) for sid in result.scene_ids]
        result.deltas = extract(
            graph, profile, policy, client,
            scenes=[s for s in new_scenes if s], use_batch=False,
        )
    else:
        result.notes.append(
            "no model client: the chapter is stored and the card's reveals are in the graph, "
            "but re-extraction has not run. Run `bp accept --llm` to write the full deltas."
        )

    if card is not None:
        for pid in card.seeds_paid:
            graph.conn.execute(
                "UPDATE promises SET status='paid', paid_in=? WHERE promise_id=?",
                (result.scene_ids[0] if result.scene_ids else "", pid),
            )
        for tid in card.threads_advanced:
            graph.conn.execute(
                "UPDATE threads SET last_scene=? WHERE thread_id=?",
                (result.scene_ids[-1] if result.scene_ids else "", tid),
            )

    graph.commit()

    if accepted_dir is not None:
        path = Path(accepted_dir) / f"{book}" / f"ch{chapter:02d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_manuscript_text(draft, book, chapter, pov, date_text, place), encoding="utf-8")
        result.notes.append(f"manuscript written to {path}")

    if git:
        result.committed = _git_commit(book, chapter, pov) or ""
    return result


def _apply_card(graph: Graph, profile: SeriesProfile, card: ChapterCard, scene_ids: list[str], span) -> None:
    """Write the card's reveals into the belief graph.

    A reveal is exactly an information transfer: what, to whom, over which
    channel. That is a report edge and a belief record — the same shape canon
    produces — so the checkers treat authored reveals and extracted ones
    identically, which is the point.
    """
    from .models import BeliefRecord, Event, Report

    cite = [Citation(scene=scene_ids[0] if scene_ids else card.card_id, quote="")]
    for i, reveal in enumerate(card.reveals, start=1):
        what = reveal.get("what", "")
        if not what:
            continue
        to = profile.canonical(reveal.get("to_whom", "") or card.pov)
        channel = reveal.get("channel", "") or "on the page"
        event_id = f"{card.card_id}-R{i}"
        graph.write_event(
            Event(
                event_id=event_id,
                summary=what,
                when=card.date_inworld,
                when_certainty="canonical",
                where=card.location,
                participants=[profile.canonical(c) for c in card.cast] or [card.pov],
                observed_by=[to] if to else [],
                beliefs=[BeliefRecord(character=to, state="knows", as_of=card.date_inworld)] if to else [],
                reports=([Report(**{"from": card.pov, "to": to, "channel": channel,
                                    "departs": card.date_inworld, "arrives": card.date_inworld})]
                         if to and to != profile.canonical(card.pov) else []),
                consequences=card.consequences_expected,
                reader_state="shown directly",
                claim_type="explicit",
                citations=cite,
            ),
            generated=True,
        )


def _manuscript_text(draft: Draft, book: str, chapter: int, pov: str, date_text: str, place: str) -> str:
    front = [
        "---",
        f"book: {book}",
        f"chapter: {chapter}",
        f"pov: {pov}",
        f"date: {date_text}",
        f"place: {place}",
        "---",
        "",
    ]
    return "\n".join(front) + draft.text.strip() + "\n"


def _next_chapter(graph: Graph, book: str) -> int:
    row = graph.conn.execute("SELECT MAX(chapter) AS m FROM scenes WHERE book_id=?", (book,)).fetchone()
    return (row["m"] or 0) + 1


def _book_ord(graph: Graph, book: str) -> int:
    row = graph.conn.execute("SELECT ord FROM books WHERE book_id=?", (book,)).fetchone()
    if row and row["ord"]:
        return row["ord"]
    row = graph.conn.execute("SELECT MAX(ord) AS m FROM books").fetchone()
    return (row["m"] or 0) + 1


def _git_commit(book: str, chapter: int, pov: str) -> str | None:
    """One commit per accepted chapter. Alternate continuations are branches, and
    a wrong turn in chapter thirty is a reset rather than a rewrite."""
    try:
        subprocess.run(["git", "add", "graph", "accepted", "plan"], check=False,
                       capture_output=True, timeout=30)
        done = subprocess.run(
            ["git", "commit", "-m", f"accept {book} ch{chapter:02d} ({pov})"],
            capture_output=True, text=True, timeout=30,
        )
        if done.returncode != 0:
            return None
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=30)
        return head.stdout.strip() or None
    except Exception:
        return None


def rollback(graph: Graph, scene_ids: list[str]) -> None:
    """Undo an acceptance in the graph. The manuscript is git's problem."""
    for sid in scene_ids:
        graph.conn.execute("DELETE FROM scenes WHERE scene_id=?", (sid,))
        graph.conn.execute("DELETE FROM scenes_fts WHERE scene_id=?", (sid,))
        graph.conn.execute("DELETE FROM chunks WHERE scene_id=?", (sid,))
        graph.conn.execute("DELETE FROM phrase_ledger WHERE scene_id=?", (sid,))
        for kind_table, id_col in (("events", "event_id"), ("objects", "object_id")):
            graph.conn.execute(
                f"""DELETE FROM {kind_table} WHERE {id_col} IN
                    (SELECT record_id FROM citations WHERE scene_id=?)""", (sid,))
        graph.conn.execute("DELETE FROM citations WHERE scene_id=?", (sid,))
    graph.commit()


def state_before(graph: Graph, profile: SeriesProfile, character: str, date_text: str) -> dict:
    """'What did this character believe going into chapter 47' — as a checkout."""
    from .knowledge import KnowledgeGraph

    span = profile.calendar.parse(date_text)
    if span is None:
        raise ValueError(f"cannot parse date {date_text!r}")
    kg = KnowledgeGraph(graph)
    snapshot = kg.belief_snapshot(character, span.lo)
    out = {"character": profile.canonical(character),
           "as_of": profile.calendar.format(span.lo),
           "location": kg.location_of(character, span.lo),
           "beliefs": {}}
    for event_id, state in snapshot.items():
        row = graph.event(event_id)
        out["beliefs"][event_id] = {"state": state, "summary": row["summary"] if row else ""}
    return out

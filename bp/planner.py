"""Stage 3 — planning, as search with a human judge.

Prior attempts asked a model for an outline and took the first answer. That
produces the median continuation: competent, predictable, slightly dull. The
median is exactly what a next-token predictor is *for*, so getting anything else
requires generating alternatives and throwing most of them away.

Three parts, and only two of them need a model:

**Series thesis** — several full-series resolutions, each scored on evidence
from the promise ledger: how many planted setups it pays, how many it orphans.

**Book skeleton** — computed, not invented. Every long series has a measurable
cadence, and the skeleton copies it.

**Move tournament** — eight to twelve candidate moves per open thread, scored by
separate judges, expanded two deep at act breaks. The top three go to the human
with their scores, their consequence edges, and the citations behind them. This
is the one place invention happens, and it is where a human should spend their
attention.

One judge deserves its own note. *Futures opened* — how many interesting states
a move creates — is the closest thing here to a computable definition of
interesting. "A and B agree to cooperate" opens almost nothing. "A learns B's
secret, B realises A knows, both pretend otherwise" opens a dozen.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field

from pydantic import BaseModel

from .db import Graph
from .llm import Usage, structured
from .models import ChapterCard, Citation, EndingHypothesis, Move
from .policy import RunPolicy
from .profile import SeriesProfile

THESIS_SYSTEM = """You are a story analyst proposing how an unfinished series ends.

You are not writing. You are arguing from evidence. Every hypothesis must be
grounded in setups the text has already planted: name the promise IDs it pays and
the ones it leaves orphaned. A resolution that orphans the series' biggest
promises is a weak hypothesis however satisfying it sounds."""

MOVE_SYSTEM = """You propose candidate moves — what could happen next in a thread.

Propose genuinely different moves, not variations on one. Include at least two that
a competent writer would not reach for first. For each, score yourself honestly on:

- setup: does it cash promises already planted?
- cost: is it irreversible? does someone lose something real?
- character_truth: would this person do this, given what the text establishes?
- inevitable_in_hindsight: surprising now, obvious later.
- futures_opened: how many interesting states does it create? A move that resolves
  a tension neatly opens almost nothing. A move that entangles people opens a lot.

Cite the scenes your reasoning rests on."""


@dataclass
class Skeleton:
    """The book's shape, measured from the corpus rather than assumed."""

    chapters: int = 0
    mean_chapter_words: float = 0.0
    sd_chapter_words: float = 0.0
    pov_slate: list[str] = field(default_factory=list)
    pov_share: dict[str, float] = field(default_factory=dict)
    rotation: list[str] = field(default_factory=list)
    act_breaks: list[int] = field(default_factory=list)
    parallel_threads: float = 0.0
    turning_point_cadence: float = 0.0

    def render(self, profile: SeriesProfile) -> str:
        shares = ", ".join(f"{p} {s:.0%}" for p, s in sorted(self.pov_share.items(), key=lambda t: -t[1]))
        return "\n".join([
            f"chapters: {self.chapters} · mean {self.mean_chapter_words:,.0f} words "
            f"(sd {self.sd_chapter_words:,.0f})",
            f"POV slate: {shares or 'none detected'}",
            f"act breaks after chapters: {', '.join(map(str, self.act_breaks)) or 'n/a'}",
            f"threads running in parallel: {self.parallel_threads:.1f}",
            f"turning point every {self.turning_point_cadence:.1f} chapters",
            "rotation rhythm: " + (" → ".join(self.rotation[:12]) + " …" if self.rotation else "n/a"),
        ])


def measure_skeleton(graph: Graph, *, book: str | None = None) -> Skeleton:
    """Copy the series' own cadence.

    Deterministic on purpose: chapter count, POV rotation, act structure and
    pacing are all *in* the corpus. Asking a model to invent them would discard
    measurements we already have.
    """
    scenes = graph.scenes(book=book, generated=False)
    if not scenes:
        return Skeleton()

    by_chapter: dict[tuple[str, int], list] = {}
    for s in scenes:
        by_chapter.setdefault((s.book_id, s.chapter), []).append(s)

    chapters = sorted(by_chapter)
    words = [sum(s.words for s in by_chapter[c]) for c in chapters]
    povs = [next((s.pov for s in by_chapter[c] if s.pov), "") for c in chapters]

    counts: dict[str, int] = {}
    for p in povs:
        if p:
            counts[p] = counts.get(p, 0) + 1
    total = sum(counts.values()) or 1

    books = sorted({b for b, _ in chapters})
    per_book = len(chapters) / max(1, len(books))

    # Act breaks: the plan's structural unit. Absent explicit part markers, the
    # convention of quarters is the least-assuming reading of a novel's shape.
    n = round(per_book)
    act_breaks = [round(n * f) for f in (0.25, 0.5, 0.75)] if n >= 8 else []

    # Turning points, cheaply: chapters whose word count is a local maximum are
    # a decent proxy for where the series puts its weight.
    turning = sum(
        1 for i in range(1, len(words) - 1) if words[i] > words[i - 1] and words[i] > words[i + 1]
    )

    thread_rows = graph.conn.execute("SELECT COUNT(*) AS n FROM threads WHERE status='open'").fetchone()

    return Skeleton(
        chapters=n,
        mean_chapter_words=statistics.fmean(words) if words else 0.0,
        sd_chapter_words=statistics.pstdev(words) if len(words) > 1 else 0.0,
        pov_slate=[p for p, _ in sorted(counts.items(), key=lambda t: -t[1])],
        pov_share={p: c / total for p, c in counts.items()},
        rotation=[p for p in povs if p][:40],
        act_breaks=act_breaks,
        parallel_threads=float(thread_rows["n"] or 0),
        turning_point_cadence=(len(chapters) / turning) if turning else 0.0,
    )


# ----------------------------------------------------------------- series thesis
class _Hypotheses(BaseModel):
    items: list[EndingHypothesis]


def propose_thesis(
    graph: Graph, profile: SeriesProfile, policy: RunPolicy, client, *, n: int = 5,
    usage: Usage | None = None,
) -> list[EndingHypothesis]:
    promises = graph.promises("open")
    ledger = "\n".join(
        f"- {p['promise_id']} [{p['kind']}, weight {p['weight']:.2f}] {p['summary']}"
        for p in sorted(promises, key=lambda r: -r["weight"])[:120]
    )
    threads = "\n".join(f"- {t['thread_id']}: {t['name']} — {t['state']}" for t in graph.threads("open")[:60])

    def ask(count: int, extra: str = "") -> list[EndingHypothesis]:
        prompt = (
            f"SERIES: {profile.name}\n\n"
            f"OPEN PROMISES (the ledger of everything the text has promised to pay off):\n{ledger}\n\n"
            f"OPEN THREADS:\n{threads}\n\n"
            f"Propose {count} genuinely different full-series resolutions. For each, list the promise IDs "
            f"it pays and the ones it orphans. Make at least two of them uncomfortable." + extra
        )
        result = structured(client, policy.model_for("plan"), _Hypotheses, system=THESIS_SYSTEM,
                            prompt=prompt, max_tokens=12_000, usage=usage, stage="plan:thesis")
        return result.items

    # `n` in the prompt is a request, not an enforced count — a model asked
    # for 2 has returned 1. Ask once more for exactly the shortfall rather
    # than silently handing back fewer hypotheses than were asked for.
    items = ask(n)
    if len(items) < n:
        missing = n - len(items)
        items = items + ask(missing, extra=f" Return exactly {missing} — no more, no fewer.")

    weights = {p["promise_id"]: p["weight"] for p in promises}
    return sorted(items, key=lambda h: -h.evidence_score(weights))[:n]


# --------------------------------------------------------------- move tournament
class _Moves(BaseModel):
    items: list[Move]


def move_tournament(
    graph: Graph,
    profile: SeriesProfile,
    policy: RunPolicy,
    client,
    *,
    thread_id: str,
    thesis: EndingHypothesis | None = None,
    breadth: int = 10,
    depth: int = 1,
    usage: Usage | None = None,
) -> list[Move]:
    """Candidate moves for one thread, expanded ``depth`` levels deep.

    Depth 2 is used at act breaks: each surviving move is expanded into its own
    next moves, and scored partly on the quality of the futures it leads to. Not
    Monte Carlo in any rigorous sense, but search rather than sampling — enough
    to stop the engine choosing a good scene that leads nowhere.
    """
    row = graph.conn.execute("SELECT * FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
    if row is None:
        raise KeyError(f"no thread {thread_id!r}")

    open_promises = "\n".join(
        f"- {p['promise_id']}: {p['summary']}" for p in graph.promises("open")[:60]
    )
    context = (
        f"SERIES: {profile.name}\n"
        f"THREAD {thread_id}: {row['name']}\n"
        f"CURRENT STATE: {row['state']}\n"
        f"CHARACTERS: {', '.join(json.loads(row['characters'] or '[]'))}\n\n"
        f"OPEN PROMISES:\n{open_promises}\n"
    )
    if thesis:
        context += f"\nSERIES THESIS THIS MUST SERVE:\n{thesis.summary}\n"

    moves = structured(
        client, policy.model_for("plan"), _Moves, system=MOVE_SYSTEM,
        prompt=context + f"\nPropose {breadth} candidate moves for this thread.",
        max_tokens=16_000, usage=usage, stage="plan:moves",
    ).items
    for i, m in enumerate(moves):
        m.move_id = m.move_id or f"{thread_id}-m{i+1}"
        m.thread_id = thread_id

    if depth > 1:
        for m in sorted(moves, key=lambda x: -x.score)[:4]:
            children = structured(
                client, policy.model_for("plan"), _Moves, system=MOVE_SYSTEM,
                prompt=(context + f"\nSuppose this happens next:\n{m.summary}\n\n"
                        f"Propose {max(3, breadth // 2)} moves that could follow it."),
                max_tokens=10_000, usage=usage, stage="plan:moves:depth2",
            ).items
            for j, c in enumerate(children):
                c.move_id = f"{m.move_id}.{j+1}"
                c.thread_id = thread_id
            m.children = children

    return sorted(moves, key=lambda m: -m.score)


def shortlist(moves: list[Move], *, n: int = 3) -> list[Move]:
    """The top n, for the human. Scores travel with them, never collapsed away."""
    return sorted(moves, key=lambda m: -m.score)[:n]


def render_shortlist(moves: list[Move]) -> str:
    lines = []
    for i, m in enumerate(moves, 1):
        lines.append(f"[{i}] {m.summary}   (score {m.score:.2f})")
        lines.append(f"     setup {m.setup:.2f} · cost {m.cost:.2f} · truth {m.character_truth:.2f} "
                     f"· hindsight {m.inevitable_in_hindsight:.2f} · futures {m.futures_opened:.2f}")
        if m.promises_cashed:
            lines.append(f"     cashes: {', '.join(m.promises_cashed)}")
        if m.consequences:
            lines.append(f"     consequences: {'; '.join(m.consequences[:3])}")
        for c in m.citations[:2]:
            lines.append(f"     cf. {c.scene}")
        if m.children:
            best = max(m.children, key=lambda c: c.score)
            lines.append(f"     leads to: {best.summary} ({best.score:.2f})")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- chapter cards
def plan_chapter(
    graph: Graph, profile: SeriesProfile, policy: RunPolicy, client, *,
    book: str, chapter: int, pov: str, move: Move | None = None,
    skeleton: Skeleton | None = None, previous: ChapterCard | None = None,
    usage: Usage | None = None,
) -> ChapterCard:
    """Turn a chosen move into the contract the drafter must honour."""
    skeleton = skeleton or measure_skeleton(graph)
    open_threads = "\n".join(f"- {t['thread_id']}: {t['name']} — {t['state']}" for t in graph.threads("open")[:40])
    state = ""
    if previous:
        state = (f"\nPREVIOUS CHAPTER CARD:\n{previous.model_dump_json(indent=2)[:2000]}\n")

    prompt = (
        f"SERIES: {profile.name}\nBOOK: {book}  CHAPTER: {chapter}  POV: {pov}\n"
        f"WORD BUDGET: {skeleton.mean_chapter_words:.0f}\n"
        + (f"\nTHE MOVE THIS CHAPTER EXECUTES:\n{move.summary}\n"
           f"Expected consequences: {'; '.join(move.consequences)}\n" if move else "")
        + f"\nOPEN THREADS:\n{open_threads}\n{state}\n"
        "Write the chapter card. It is a contract, not prose. Every reveal you list will be "
        "written into the belief graph when the chapter is accepted, so list exactly the "
        "information that changes hands and to whom. Break the chapter into 2–4 scenes with beats."
    )
    card = structured(client, policy.model_for("plan"), ChapterCard, prompt=prompt,
                      max_tokens=8000, usage=usage, stage="plan:card")
    card.card_id = card.card_id or f"{book}.ch{chapter:02d}"
    card.book, card.chapter, card.pov = book, chapter, pov
    if not card.word_budget:
        card.word_budget = int(skeleton.mean_chapter_words or 3000)
    graph.write_card(card)
    graph.commit()
    return card

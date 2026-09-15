"""Stage 4 — drafting from a context pack assembled by query.

The pack is the whole architecture in miniature. It starts small, it is ordered
so its stable half can be cached across every call in the book, and it grows
only when a checker's marginalia asks for more.

More context is not free. Past a point it is a conference room full of documents
for one conversation: the model has everything and attends to the wrong thing.
Where that point sits is an empirical question, which is why the backtest runs a
halve-the-pack ablation rather than this module picking a number and defending
it.

Voice comes from **retrieved exemplars**, not from fine-tuning and not from
"write like the author". Canon passages in the same POV, matched on situation and
technique tags, show the model how this POV handles this kind of moment. Nothing
is trained on the text.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .db import Graph
from .knowledge import KnowledgeGraph
from .llm import Usage, write
from .models import ChapterCard, TechniqueSpec
from .policy import RunPolicy
from .profile import SeriesProfile
from .retrieval import Retriever
from .textstats import estimate_tokens

ENGINE_RULES = """You are drafting one scene of a novel in an established series.

Hard rules:
- Write only the scene. No preamble, no summary, no notes, no headings.
- Do not reveal anything the chapter card does not list. Uncarded reveals corrupt the
  story bible when this chapter is accepted.
- A character may only act on information they could have. The context pack states what
  this POV knows and when they learned it. If a beat seems to need knowledge they lack,
  write around it — do not grant it.
- Reuse none of the phrases in the do-not-reuse ledger.
- Match the POV's technique, not the author's average. The exemplars show how this POV
  handles this kind of moment; the spec says why.
- Write to the word budget."""


@dataclass
class ContextPack:
    """The pack in render order: stable prefix first, so caching works."""

    rules: str = ""
    bible_digest: str = ""
    style_spec: str = ""
    card_block: str = ""
    state_block: str = ""
    exemplars: str = ""
    recent: str = ""
    phrase_ledger: str = ""
    scene_brief: str = ""
    sources: list[str] = field(default_factory=list)

    @property
    def stable(self) -> list[str]:
        """Unchanged within a book, and the half that carries the breakpoint.

        Everything downstream of this list is per-chapter, so this is the
        boundary a cache read has to land on. See :func:`bp.llm.write`.
        """
        return [b for b in (self.rules, self.bible_digest, self.style_spec) if b]

    @property
    def per_chapter(self) -> list[str]:
        return [b for b in (self.card_block, self.state_block, self.exemplars,
                            self.recent, self.phrase_ledger) if b]

    def system_blocks(self) -> list[str]:
        return self.stable + self.per_chapter

    @property
    def tokens(self) -> int:
        return sum(estimate_tokens(b) for b in self.system_blocks() + [self.scene_brief])

    def render(self) -> str:
        return "\n\n".join(self.system_blocks() + [self.scene_brief])


def bible_digest(graph: Graph, profile: SeriesProfile, *, budget_tokens: int = 8_000) -> str:
    """A compressed world model, regenerated per act.

    Not a summary of the plot — a summary of *state*: who is alive, what is
    unresolved, what has been promised. The plot lives in the graph and is
    retrieved per scene.
    """
    parts = [f"SERIES BIBLE DIGEST — {profile.name}", ""]
    parts.append("PRINCIPALS")
    for row in graph.conn.execute(
        "SELECT name, status, substrate, description FROM entities WHERE kind='character' ORDER BY name LIMIT 60"
    ):
        bits = [row["name"], f"({row['status']})"]
        if row["substrate"]:
            bits.append(f"on {row['substrate']}")
        if row["description"]:
            bits.append("— " + row["description"][:140])
        parts.append("  " + " ".join(bits))

    parts += ["", "OPEN THREADS"]
    for row in graph.threads("open")[:30]:
        parts.append(f"  {row['thread_id']}: {row['name']} — {row['state'][:160]}")

    parts += ["", "OPEN PROMISES (weightiest first)"]
    for row in sorted(graph.promises("open"), key=lambda r: -r["weight"])[:30]:
        parts.append(f"  {row['promise_id']} [{row['kind']}]: {row['summary'][:160]}")

    unresolved = graph.contradictions(unresolved_only=True)
    if unresolved:
        parts += ["", "UNRESOLVED IN CANON (do not settle these by accident)"]
        for row in unresolved[:12]:
            parts.append(f"  {row['subject']}: {row['reading_a']} / {row['reading_b']}")

    text = "\n".join(parts)
    while estimate_tokens(text) > budget_tokens and len(parts) > 20:
        parts = parts[: int(len(parts) * 0.85)]
        text = "\n".join(parts)
    return text


def style_block(spec: TechniqueSpec | None, pov: str) -> str:
    if spec is None:
        return f"TECHNIQUE SPEC — {pov}\n  (none extracted yet)"
    lines = [
        f"TECHNIQUE SPEC — {spec.pov}",
        "  measured (match these; they are computed from canon):",
        f"    sentence length {spec.mean_sentence_len:.1f} words, spread {spec.sd_sentence_len:.1f}",
        f"    dialogue {spec.dialogue_ratio:.0%} of words · interiority {spec.interiority_ratio:.1%}"
        f" · sensory {spec.sensory_density:.1%}",
        f"    paragraphs average {spec.paragraph_len:.0f} words",
    ]
    if spec.narrative_distance:
        lines += ["  mechanism:", f"    narrative distance: {spec.narrative_distance}"]
    if spec.exposition_mode:
        lines.append(f"    exposition: {spec.exposition_mode}")
    if spec.emotion_mode:
        lines.append(f"    emotion: {spec.emotion_mode}")
    if spec.devices:
        lines.append(f"    devices: {', '.join(spec.devices[:8])}")
    if spec.tics:
        lines.append(f"    verbal tics: {', '.join(spec.tics[:8])}")
    if spec.drifted_from:
        lines.append(f"    drift from origin voice: {spec.drifted_from}")
    return "\n".join(lines)


def knowledge_block(graph: Graph, profile: SeriesProfile, pov: str, day: float | None) -> str:
    """What this POV knows, and what they believe that is false.

    This block is why the drafter does not need the books. The model is not
    asked to remember what a character learned in book two; it is told, with
    dates.
    """
    if day is None:
        return f"POV STATE — {pov}\n  (chapter has no in-world date; knowledge state unavailable)"
    kg = KnowledgeGraph(graph)
    snapshot = kg.belief_snapshot(pov, day)
    knows, unaware, false = [], [], []
    for event_id, state in snapshot.items():
        row = graph.event(event_id)
        if row is None:
            continue
        label = f"{row['summary'][:120]}"
        if state == "knows":
            knows.append(label)
        elif state in {"believes_false", "misinformed"}:
            false.append(label)
        else:
            unaware.append(label)

    place = kg.location_of(pov, day)
    ent = graph.entity(pov)
    lines = [f"POV STATE — {pov}, as of {profile.calendar.format(day)}"]
    if place:
        lines.append(f"  location: {place}")
    if ent is not None and ent["substrate"]:
        lines.append(f"  substrate: {ent['substrate']}")
    if knows:
        lines += ["  knows:"] + [f"    - {k}" for k in knows[:25]]
    if false:
        lines += ["  believes, wrongly:"] + [f"    - {k}" for k in false[:15]]
    if unaware:
        lines += ["  does NOT yet know (do not let them act on these):"] + [f"    - {k}" for k in unaware[:25]]
    return "\n".join(lines)


def build_pack(
    graph: Graph,
    profile: SeriesProfile,
    policy: RunPolicy,
    card: ChapterCard,
    *,
    scene_index: int = 1,
    budget_tokens: int | None = None,
    extra: list[str] | None = None,
) -> ContextPack:
    budget = budget_tokens or policy.context_start_tokens
    retriever = Retriever(graph)
    pov = profile.canonical(card.pov)
    day = None
    if card.date_inworld:
        try:
            span = profile.calendar.parse(card.date_inworld)
            day = span.lo if span else None
        except Exception:
            day = None

    scene_spec = card.scenes[scene_index - 1] if 0 < scene_index <= len(card.scenes) else {}
    situation = str(scene_spec.get("beats", card.turn or card.goal))
    techniques = [t.strip() for t in str(scene_spec.get("techniques", "")).split(",") if t.strip()]

    exemplars = retriever.exemplars(pov, situation=situation, techniques=techniques, limit=6)
    ledger = retriever.phrase_ledger(before_ord=10**9)
    last_ord = graph.conn.execute("SELECT MAX(ord) AS m FROM scenes").fetchone()["m"] or 0
    recent = retriever.previous_chapters(last_ord + 1, limit=3)
    same_pov = retriever.previous_in_pov(pov, last_ord + 1)

    pack = ContextPack(
        rules=ENGINE_RULES,
        bible_digest=bible_digest(graph, profile),
        style_spec=style_block(graph.style(pov), pov),
        card_block="CHAPTER CARD (binding)\n" + card.model_dump_json(indent=2),
        state_block=knowledge_block(graph, profile, pov, day),
        exemplars=_exemplar_block(exemplars),
        recent=_recent_block(recent, same_pov),
        phrase_ledger=("DO NOT REUSE — distinctive phrases from the last 40k words:\n  "
                       + "; ".join(f"“{p}”" for p in ledger[:80])) if ledger else "",
        scene_brief=_scene_brief(card, scene_index),
        sources=[h.scene_id for h in exemplars] + [h.scene_id for h in recent],
    )
    for block in extra or []:
        pack.state_block += "\n\n" + block

    _fit(pack, budget)
    return pack


def _exemplar_block(hits) -> str:
    if not hits:
        return ""
    parts = ["CANON EXEMPLARS — how this POV handles this kind of moment.",
             "Study the technique. Do not copy sentences."]
    for h in hits:
        parts.append(f"\n--- {h.scene_id} ({h.why}) ---\n{h.text[:2200]}")
    return "\n".join(parts)


def _recent_block(recent, same_pov) -> str:
    parts = ["IMMEDIATELY BEFORE THIS CHAPTER"]
    for h in reversed(recent):
        parts.append(f"\n--- {h.scene_id} ({h.pov}) ---\n{h.text[-1500:]}")
    if same_pov is not None:
        parts.append(f"\n--- previous chapter in this POV: {same_pov.scene_id} ---\n{same_pov.text[-1500:]}")
    return "\n".join(parts)


def _scene_brief(card: ChapterCard, scene_index: int) -> str:
    total = max(1, len(card.scenes))
    spec = card.scenes[scene_index - 1] if 0 < scene_index <= len(card.scenes) else {}
    budget = spec.get("word_budget") or (card.word_budget // total)
    beats = spec.get("beats", card.turn or card.goal)
    return (f"WRITE SCENE {scene_index} OF {total}.\n"
            f"Beats: {beats}\n"
            f"Word budget: {budget}\n"
            f"POV: {card.pov} · {card.location} · {card.date_inworld}")


def _fit(pack: ContextPack, budget: int) -> None:
    """Trim to budget, cheapest material first.

    Order of sacrifice is deliberate: the phrase ledger and the recent-chapter
    tail are conveniences; the knowledge block and the card are load-bearing and
    are never trimmed. A pack that drops what a character knows in order to fit
    more prose has defeated its own purpose.
    """
    for attr, floor in (("phrase_ledger", 0), ("recent", 1200), ("exemplars", 1500),
                        ("bible_digest", 2000)):
        if pack.tokens <= budget:
            return
        text = getattr(pack, attr)
        if not text:
            continue
        over = pack.tokens - budget
        keep_chars = max(floor, len(text) - int(over * 3.8))
        setattr(pack, attr, text[:keep_chars] + ("\n… (trimmed to fit context budget)" if keep_chars < len(text) else ""))


# ------------------------------------------------------------------- generation
@dataclass
class Candidate:
    index: int
    text: str
    pack_tokens: int
    scorecard: object | None = None

    @property
    def words(self) -> int:
        from .textstats import words as _w

        return len(_w(self.text))


def draft_scene(
    graph: Graph,
    profile: SeriesProfile,
    policy: RunPolicy,
    client,
    card: ChapterCard,
    *,
    scene_index: int = 1,
    n_candidates: int | None = None,
    budget_tokens: int | None = None,
    extra_context: list[str] | None = None,
    usage: Usage | None = None,
) -> list[Candidate]:
    """Draft one scene ``n`` times. Candidates are ranked before a human sees them."""
    n = n_candidates or policy.candidates_per_scene
    pack = build_pack(graph, profile, policy, card, scene_index=scene_index,
                      budget_tokens=budget_tokens, extra=extra_context)
    out: list[Candidate] = []
    for i in range(n):
        text = write(
            client, policy.model_for("draft"),
            stable_blocks=pack.stable,
            tail_blocks=pack.per_chapter,
            prompt=pack.scene_brief,
            max_tokens=min(32_000, max(4_000, card.word_budget * 3)),
            usage=usage, stage="draft",
            thinking_effort=policy.thinking_effort,
            fallback_model=policy.model_for("plan"),
        )
        out.append(Candidate(index=i + 1, text=text.strip(), pack_tokens=pack.tokens))
    return out


def revise(
    graph: Graph, profile: SeriesProfile, policy: RunPolicy, client, card: ChapterCard,
    *, text: str, marginalia: list, scene_index: int = 1, human_note: str = "",
    usage: Usage | None = None,
) -> str:
    """Send a scene back with its marginalia attached, and — when a human
    rejected it through ``bp check --serve`` — the reason they gave.

    ``human_note`` is first-class input, not context: a rejection that only
    says "no" teaches the reviser nothing the checkers' own marginalia didn't
    already say, and it is the one channel through which taste ("this should
    feel quieter, and end badly") can reach the prose at all.

    The pack grows here — a failed information-path check pulls in the channel
    and distance data it was decided on, a voice failure pulls in more
    exemplars — which is the only place ``context.max_tokens`` gets used.
    """
    notes = "\n".join(
        f"- [{m.severity}] {m.check} (line {m.line}): {m.message}"
        + ("".join(f"\n    fix: {f}" for f in m.fixes) if m.fixes else "")
        for m in marginalia
    )
    pack = build_pack(graph, profile, policy, card, scene_index=scene_index,
                      budget_tokens=policy.context_max_tokens)
    human_block = (
        f"\n\n--- EDITOR'S NOTE (why a human sent this back — follow it) ---\n{human_note.strip()}"
        if human_note.strip() else ""
    )
    prompt = (
        f"{pack.scene_brief}\n\n"
        f"Here is your draft, and an editor's marginalia. Fix every hard finding, and honour the "
        f"editor's note below if there is one. Change nothing else the marginalia or the note did "
        f"not ask about.\n\n"
        f"--- DRAFT ---\n{text}\n\n--- MARGINALIA ---\n{notes}{human_block}"
    )
    return write(client, policy.model_for("draft"),
                 stable_blocks=pack.stable, tail_blocks=pack.per_chapter,
                 prompt=prompt, max_tokens=32_000, usage=usage, stage="draft:revise",
                 thinking_effort=policy.thinking_effort).strip()

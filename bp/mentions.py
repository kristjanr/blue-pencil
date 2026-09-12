"""Finding what a draft refers to — the one place a hard check needs a model.

The hard checkers are otherwise pure arithmetic. But before you can ask "could
Riker know about Milo's destruction on this date", something has to notice that
the chapter *mentions* Milo's destruction. That is genuinely a language problem.

So mention detection is layered, and the layers are honest about their strength:

1. **Lexical recall** (always on, no API key). Keyword signatures from each
   event's summary, entity names and aliases resolved through the profile. Tuned
   for recall — it is a candidate generator.
2. **Model precision** (``--llm``, optional). A cheap model classifies each
   candidate as a real reference or a coincidence. This is the "model only
   classifies mentions" line in the cost table, and it is the whole model spend
   on the hard checks.

Running layer 1 alone gives more false alarms and misses paraphrase. Running it
with layer 2 costs cents per chapter. Neither is allowed to be *required*,
because a continuity editor that cannot run offline cannot run in a loop.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .db import Graph
from .draftdoc import Draft
from .textstats import _STOP

_TOKEN = re.compile(r"[A-Za-z][A-Za-z'\-]+")
#: Verbs and nouns too generic to signal a specific event.
_WEAK = frozenset("""
said says say went go goes came come get got make made take took give gave know knew
thing things time day way people man woman one two new old good bad great little long
""".split()) | _STOP


@dataclass
class Mention:
    kind: str                # event | entity | object
    target_id: str
    label: str
    char_index: int
    line: int
    excerpt: str
    matched: list[str] = field(default_factory=list)
    strength: float = 0.0
    confirmed: bool | None = None    # None == not model-checked

    @property
    def credible(self) -> bool:
        return self.confirmed is not False


def _signature(text: str, *, limit: int = 6) -> list[str]:
    """Distinctive content words from a summary, longest first."""
    toks = [t.lower() for t in _TOKEN.findall(text)]
    seen: list[str] = []
    for t in toks:
        if len(t) < 4 or t in _WEAK or t in seen:
            continue
        seen.append(t)
    seen.sort(key=len, reverse=True)
    return seen[:limit]


def find_event_mentions(draft: Draft, graph: Graph, *, min_terms: int = 3,
                        min_share: float = 0.5) -> list[Mention]:
    """Candidate references to known events, with line numbers.

    Tuned by what goes wrong. A first cut that fired on any two shared words
    flagged a chapter saying "Vela had gone quiet" as a reference to "the Vela
    relay is destroyed" — the place name plus one common adjective. Those
    coincidences are fatal: a continuity editor that cries wolf twice a chapter
    stops being read, and then it catches nothing at all.

    So a candidate must clear three bars together:

    1. at least ``min_terms`` distinct signature terms, all inside one 600-char
       neighbourhood — a real reference is local, a coincidence is scattered;
    2. at least ``min_share`` of the event's whole signature, so matching two
       words of a six-word summary is not enough;
    3. at least one *anchoring* term — a participant's name, or a word rare
       enough in the draft to carry information on its own.

    The model precision pass tightens this further; these bars are what the
    offline path has to survive on.
    """
    out: list[Mention] = []
    profile = graph.profile
    for row in graph.events():
        sig = _signature(row["summary"], limit=8)
        if len(sig) < 2:
            continue

        names = json.loads(row["participants"] or "[]") + json.loads(row["observed_by"] or "[]")
        anchors: set[str] = set()
        for n in names:
            anchors.update(a.lower() for a in (profile.alias_set(n) if profile else [n]))

        terms = set(sig) | anchors
        positions: dict[str, list[int]] = {}
        for term in terms:
            for m in re.finditer(rf"\b{re.escape(term)}\w{{0,3}}\b", draft.text, re.I):
                positions.setdefault(term, []).append(m.start())
        if len(positions) < min_terms:
            continue

        best = _best_window(positions)
        if best is None:
            continue
        start, window_terms = best
        if len(window_terms) < min_terms:
            continue
        if not (window_terms & anchors) and not any(len(t) >= 7 for t in window_terms):
            continue  # no anchoring term: this is vocabulary overlap, not a reference
        share = len(window_terms & set(sig)) / len(set(sig))
        if share < min_share:
            continue

        out.append(Mention(
            kind="event", target_id=row["event_id"], label=row["summary"],
            char_index=start, line=draft.line_of(start), excerpt=draft.excerpt(start),
            matched=sorted(window_terms), strength=round(share, 3),
        ))
    return sorted(out, key=lambda m: (-m.strength, m.line))


def _best_window(positions: dict[str, list[int]], *, width: int = 600) -> tuple[int, set[str]] | None:
    """The 600-character neighbourhood containing the most distinct terms.

    Locality is the whole discriminator between a reference and a coincidence:
    a chapter that genuinely refers to an event puts its words together.
    """
    starts = sorted(p for ps in positions.values() for p in ps)
    best: tuple[int, set[str]] | None = None
    for start in starts:
        here = {t for t, ps in positions.items() if any(start <= p <= start + width for p in ps)}
        if best is None or len(here) > len(best[1]):
            best = (start, here)
    return best


def find_entity_mentions(draft: Draft, graph: Graph) -> list[Mention]:
    out: list[Mention] = []
    profile = graph.profile
    for row in graph.entities():
        for alias in (profile.alias_set(row["name"]) if profile else [row["name"]]):
            m = re.search(rf"\b{re.escape(alias)}\b", draft.text)
            if m:
                out.append(Mention("entity", row["entity_id"], row["name"], m.start(),
                                   draft.line_of(m.start()), draft.excerpt(m.start()), [alias], 1.0))
                break
    return out


def find_object_mentions(draft: Draft, graph: Graph) -> list[Mention]:
    out: list[Mention] = []
    for row in graph.objects():
        names = [row["name"], *json.loads(row["aliases"] or "[]")]
        for name in names:
            if len(name) < 3:
                continue
            m = re.search(rf"\b{re.escape(name)}\b", draft.text, re.I)
            if m:
                out.append(Mention("object", row["object_id"], row["name"], m.start(),
                                   draft.line_of(m.start()), draft.excerpt(m.start()), [name], 1.0))
                break
    return out


def confirm_with_model(mentions: list[Mention], draft: Draft, client, model: str) -> list[Mention]:
    """Ask a cheap model which candidates are real references.

    Precision only. A candidate the model rejects is dropped; it is never asked
    to *add* mentions, because a hard checker acting on a hallucinated reference
    is worse than one that misses a paraphrase.
    """
    from .llm import classify_mentions

    if not mentions:
        return mentions
    verdicts = classify_mentions(client, model, draft.text, [
        {"id": m.target_id, "label": m.label, "excerpt": m.excerpt} for m in mentions
    ])
    for m in mentions:
        if m.target_id in verdicts:
            m.confirmed = verdicts[m.target_id]
    return [m for m in mentions if m.credible]

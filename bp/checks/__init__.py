"""The blue pencils.

Each checker reads a draft plus the graph and emits marginalia: a location in
the text, a severity, a reason, and where possible the citation it conflicts
with. Hard failures send a scene back to drafting with the marginalia attached.
Soft ones accumulate for the human as a scorecard.

The scorecard is **never collapsed into one number**. A single score hides
exactly the trade-offs an editor needs to see — a chapter can be flawless on
continuity and dead on the page, and one figure covering both is worse than
useless.

Two rules every checker follows:

*Abstain rather than accuse.* If the graph lacks the data to decide — an undated
event, an unknown distance — the checker says so as a note. False alarms are how
a tool like this loses its reader.

*Say what to do.* A finding carries fixes. A checker that reports only "this is
wrong" makes the human do the diagnosis twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..db import Graph
from ..draftdoc import Draft
from ..knowledge import KnowledgeGraph
from ..models import ChapterCard, Marginalium
from ..policy import RunPolicy
from ..profile import SeriesProfile
from ..retrieval import Retriever
from ..timeline import Span


@dataclass
class CheckContext:
    """Everything a checker may read. Checkers never write."""

    draft: Draft
    graph: Graph
    profile: SeriesProfile
    policy: RunPolicy
    card: ChapterCard | None = None
    client: object | None = None          # anthropic client, when --llm is on
    _kg: KnowledgeGraph | None = None
    _retriever: Retriever | None = None
    _mentions: dict[str, list] = field(default_factory=dict)

    @property
    def kg(self) -> KnowledgeGraph:
        if self._kg is None:
            self._kg = KnowledgeGraph(self.graph)
        return self._kg

    @property
    def retriever(self) -> Retriever:
        if self._retriever is None:
            self._retriever = Retriever(self.graph)
        return self._retriever

    @property
    def date(self) -> Span | None:
        """The draft's in-world date, from front matter or its card."""
        text = self.draft.date_text or (self.card.date_inworld if self.card else "")
        if not text:
            return None
        try:
            return self.profile.calendar.parse(text)
        except Exception:
            return None

    @property
    def pov(self) -> str:
        return self.profile.canonical(self.draft.pov or (self.card.pov if self.card else ""))

    def mentions(self, kind: str) -> list:
        """Mention detection, computed once per chapter and shared."""
        if kind not in self._mentions:
            from .. import mentions as M

            finder = {
                "event": M.find_event_mentions,
                "entity": M.find_entity_mentions,
                "object": M.find_object_mentions,
            }[kind]
            found = finder(self.draft, self.graph)
            if self.client is not None and kind == "event":
                found = M.confirm_with_model(
                    found, self.draft, self.client, self.policy.model_for("checks")
                )
            self._mentions[kind] = found
        return self._mentions[kind]

    def fmt(self, day: float | None) -> str:
        return self.profile.calendar.format(day) if day is not None else "unknown"


class Check(Protocol):
    name: str

    def run(self, ctx: CheckContext) -> list[Marginalium]: ...


_REGISTRY: dict[str, Callable[[], Check]] = {}


def register(name: str) -> Callable[[type], type]:
    def deco(cls: type) -> type:
        cls.name = name  # type: ignore[attr-defined]
        _REGISTRY[name] = cls  # type: ignore[assignment]
        return cls

    return deco


def all_checks() -> dict[str, Check]:
    from . import (  # noqa: F401  (import for side-effect registration)
        card, consequence, discriminator, epistemic, geography, objects, panel, repetition, voice,
    )

    return {name: cls() for name, cls in _REGISTRY.items()}


@dataclass
class Scorecard:
    """Marginalia, grouped — never summed."""

    marginalia: list[Marginalium] = field(default_factory=list)
    ran: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def hard(self) -> list[Marginalium]:
        return [m for m in self.marginalia if m.severity == "hard"]

    @property
    def soft(self) -> list[Marginalium]:
        return [m for m in self.marginalia if m.severity == "soft"]

    @property
    def notes(self) -> list[Marginalium]:
        return [m for m in self.marginalia if m.severity == "note"]

    @property
    def clean(self) -> bool:
        """Clean means every *hard* check passed. Soft findings are for a human."""
        return not self.hard

    def by_check(self) -> dict[str, list[Marginalium]]:
        out: dict[str, list[Marginalium]] = {}
        for m in self.marginalia:
            out.setdefault(m.check, []).append(m)
        return out

    def render(self, *, verbose: bool = True) -> str:
        lines = []
        counts = {k: len(v) for k, v in self.by_check().items()}
        head = f"{len(self.hard)} hard · {len(self.soft)} soft · {len(self.notes)} notes"
        lines.append(head)
        if self.skipped:
            lines.append("stood down: " + ", ".join(f"{k} ({v})" for k, v in self.skipped.items()))
        if not verbose:
            lines.append("  " + ", ".join(f"{k}={n}" for k, n in sorted(counts.items())))
            return "\n".join(lines)
        order = {"hard": 0, "soft": 1, "floor": 2, "note": 3}
        for m in sorted(self.marginalia, key=lambda x: (order.get(x.severity, 9), x.line)):
            tag = m.severity.upper().ljust(5)
            lines.append(f"  [{tag}] {m.check} · line {m.line} — {m.message}")
            if m.excerpt:
                lines.append(f"          “{m.excerpt[:110]}”")
            for c in m.citations[:3]:
                lines.append(f"          cf. {c.scene}" + (f": “{c.quote[:70]}”" if c.quote else ""))
            for i, fix in enumerate(m.fixes, 1):
                lines.append(f"          fix {i}: {fix}")
        return "\n".join(lines)


def run_checks(ctx: CheckContext, *, only: list[str] | None = None) -> Scorecard:
    """Run every enabled checker and collect the marginalia.

    Severity comes from the run policy, not from the checker: the same finding
    is a hard stop in one run and a note in another, and that is the editor's
    call rather than ours. A checker that raises is reported as stood-down
    rather than taking the chapter down with it.

    The one severity a checker keeps for itself is ``note``, and that asymmetry
    is load-bearing. **A checker may only report a finding it has positively
    computed; not-found is always a note.** Every false-alarm class found so
    far has been the same mistake — reading absence of evidence as evidence of
    absence: an uncited second reading counted as a contradiction, a quote
    missing from the graph counted as fabricated, a name mentioned counted as
    present, and an information path the graph never recorded counted as an
    impossible one. That last cost 161 of 168 hard findings on the first real
    run. The deterministic arithmetic has never been wrong; deciding what
    silence means is what keeps failing. So a checker that cannot compute an
    answer says ``note``, and the policy never promotes it — the editor cannot
    be handed a hard stop the checker could not actually demonstrate.
    """
    card = Scorecard()
    for name, check in all_checks().items():
        if only and name not in only:
            continue
        cfg = ctx.policy.check(name)
        if not cfg.enabled:
            card.skipped[name] = "off in run policy"
            continue
        try:
            found = check.run(ctx)
        except Exception as exc:  # a broken checker must not block the chapter
            card.skipped[name] = f"error: {type(exc).__name__}: {exc}"
            continue
        card.ran.append(name)
        for m in found:
            # 'note' is the checker abstaining; the policy never promotes it.
            if m.severity != "note":
                m.severity = cfg.severity if cfg.severity != "off" else "note"  # type: ignore[assignment]
            card.marginalia.append(m)
    return card

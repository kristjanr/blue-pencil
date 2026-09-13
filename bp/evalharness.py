"""Evaluation — the part that decides whether any of the rest of this works.

Everything upstream could be built and still produce plausible garbage. The way
to find out is to hide a book that exists and see whether the engine could have
written it. But there is a catch: **the model may have read it.** Any series
famous enough to have a wiki is in every frontier model's training data, and a
naive "did we reproduce the real book" score looks spectacular for entirely the
wrong reason.

So: a control, and three tiers.

**Control — the contamination probe.** Before any run, ask the model to outline
the hidden book with no corpus and no graph. Whatever it recalls is the
baseline. Plot recall only counts to the extent it *beats* that baseline, and
any event the bare model already knew is excluded. This does not make the corpus
clean; it makes the contamination measurable, which is the most that is
honestly available.

**Tier A — shape.** Does the generated book have the series' proportions? POV
distribution, chapter lengths, threads opened versus closed, cadence. Memory
cannot game a distribution: a model can recite a plot, but it cannot recite a
histogram.

**Tier B — constraints.** Does the generated book obey every constraint a
legitimate next volume must? Every belief has an information path, nobody is in
two places, dead stays dead. This is the graph and the checkers scoring
themselves, and it needs **no answer key at all** — which makes it the only tier
that works on a series with no hidden book.

**Tier C — genuinely blind.** A hidden book published after the model's training
cutoff. The only tier that tests prediction rather than reconstruction, and one
honest run of it is worth more than any number of runs on a famous series.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from .checks import CheckContext, Scorecard, run_checks
from .db import Graph
from .draftdoc import Draft
from .llm import Usage, structured
from .planner import Skeleton, measure_skeleton
from .policy import RunPolicy
from .profile import SeriesProfile
from .textstats import words


# --------------------------------------------------------------------- control
class _Outline(BaseModel):
    knows_the_book: bool
    #: An unexplained field gets answered in whichever sense the model picks.
    #: Asked bare, it returned 0.90 meaning "sure that I do NOT know this",
    #: which the consumer read as "sure that it DOES" and inverted the verdict.
    #: Say which sense is wanted.
    confidence: float = Field(
        description="0-1: how confident you are in the knows_the_book answer "
                    "itself, whichever way that answer went."
    )
    chapters: list[str] = []
    key_events: list[str] = []
    note: str = ""


@dataclass
class ProbeResult:
    book: str
    knows: bool
    confidence: float
    recalled_events: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def blind(self) -> bool:
        """A bare-model outline that comes back empty is the admission ticket
        for Tier C.

        Empty hands are the evidence, not the self-report. A model that claims
        to know the book but recalls nothing is being agreeable; one that denies
        knowing it and then lists real events is contaminated regardless of what
        it said. Confidence only guards against a hedged denial — it is not the
        measurement, because its scale was read backwards once already and a
        benchmark that declares itself void on a misread float is worse than no
        benchmark.
        """
        return not self.knows and not self.recalled_events and self.confidence >= 0.5

    def render(self) -> str:
        verdict = ("BLIND — this volume qualifies as a Tier C corpus"
                   if self.blind else
                   "CONTAMINATED — the bare model already knows this volume; Tier C must "
                   "go shopping for a post-cutoff series. Tiers A and B still run here.")
        return "\n".join([
            f"contamination probe · {self.book}",
            f"  model claims knowledge: {self.knows} (confidence {self.confidence:.2f})",
            f"  events recalled with no corpus: {len(self.recalled_events)}",
            *[f"    - {e}" for e in self.recalled_events[:10]],
            f"  {verdict}",
        ])


def probe(client, policy: RunPolicy, *, series: str, book: str, usage: Usage | None = None) -> ProbeResult:
    """Ask the bare model what it already knows. No corpus, no graph."""
    result = structured(
        client, policy.model_for("probe"), _Outline,
        system=("You are being asked what you already know, with no reference material. "
                "Do not speculate or reconstruct from genre convention. If you do not know "
                "the book, say so — a confident guess is worse than an admission here, "
                "because this measurement decides whether a benchmark is valid."),
        prompt=(f"Series: {series}\nVolume: {book}\n\n"
                f"Do you know this specific volume? If so, outline it chapter by chapter and "
                f"list its key events. If you do not know it, say so plainly and leave the "
                f"lists empty."),
        max_tokens=6000, usage=usage, stage="eval:probe",
    )
    return ProbeResult(book=book, knows=result.knows_the_book,
                       confidence=max(0.0, min(1.0, result.confidence)),
                       recalled_events=result.key_events, note=result.note)


# ---------------------------------------------------------------------- tier A
@dataclass
class ShapeReport:
    """Distributional comparison. The metrics memory cannot fake."""

    metrics: dict[str, tuple[float, float]] = field(default_factory=dict)  # name -> (generated, real)
    pov_divergence: float = 0.0
    length_divergence: float = 0.0
    verdict: str = ""

    def render(self) -> str:
        lines = ["Tier A — shape"]
        for name, (gen, real) in sorted(self.metrics.items()):
            delta = (gen - real) / real if real else 0.0
            lines.append(f"  {name:26} generated {gen:>10,.1f}   real {real:>10,.1f}   {delta:+.0%}")
        lines.append(f"  POV distribution divergence   {self.pov_divergence:.3f}  (0 = identical)")
        lines.append(f"  chapter-length divergence     {self.length_divergence:.3f}")
        lines.append(f"  {self.verdict}")
        return "\n".join(lines)


def _js_divergence(p: dict[str, float], q: dict[str, float]) -> float:
    """Jensen–Shannon divergence between two categorical distributions."""
    keys = set(p) | set(q)
    if not keys:
        return 0.0
    total_p = sum(p.values()) or 1.0
    total_q = sum(q.values()) or 1.0

    def kl(a: dict[str, float], b: dict[str, float], ta: float, tb: float) -> float:
        out = 0.0
        for k in keys:
            pa = a.get(k, 0.0) / ta
            pb = b.get(k, 0.0) / tb
            if pa > 0 and pb > 0:
                out += pa * math.log2(pa / pb)
        return out

    m = {k: (p.get(k, 0.0) / total_p + q.get(k, 0.0) / total_q) / 2 for k in keys}
    return 0.5 * kl(p, m, total_p, 1.0) + 0.5 * kl(q, m, total_q, 1.0)


def tier_a(generated: Skeleton, real: Skeleton, *, tolerance: float = 0.25) -> ShapeReport:
    report = ShapeReport()
    report.metrics = {
        "chapters": (generated.chapters, real.chapters),
        "mean chapter words": (generated.mean_chapter_words, real.mean_chapter_words),
        "chapter length spread": (generated.sd_chapter_words, real.sd_chapter_words),
        "POVs in rotation": (len(generated.pov_slate), len(real.pov_slate)),
        "threads in parallel": (generated.parallel_threads, real.parallel_threads),
        "chapters per turning point": (generated.turning_point_cadence, real.turning_point_cadence),
    }
    report.pov_divergence = _js_divergence(generated.pov_share, real.pov_share)
    report.length_divergence = abs(generated.mean_chapter_words - real.mean_chapter_words) / (
        real.mean_chapter_words or 1.0
    )
    within = sum(
        1 for gen, actual in report.metrics.values()
        if actual and abs(gen - actual) / actual <= tolerance
    )
    report.verdict = (
        f"{within}/{len(report.metrics)} metrics within {tolerance:.0%}; "
        f"POV divergence {'within' if report.pov_divergence < 0.15 else 'outside'} tolerance"
    )
    return report


# ---------------------------------------------------------------------- tier B
@dataclass
class ConstraintReport:
    """The graph and the checkers scoring themselves. No answer key needed."""

    chapters: int = 0
    words: int = 0
    hard: int = 0
    soft: int = 0
    notes: int = 0
    by_check: dict[str, int] = field(default_factory=dict)

    @property
    def violations_per_10k(self) -> float:
        return self.hard * 10_000 / self.words if self.words else 0.0

    def render(self) -> str:
        lines = [
            "Tier B — constraints (needs no answer key)",
            f"  {self.chapters} chapters · {self.words:,} words",
            f"  hard violations {self.hard} ({self.violations_per_10k:.2f} per 10k words)"
            f" · soft {self.soft} · notes {self.notes}",
        ]
        for check, n in sorted(self.by_check.items(), key=lambda t: -t[1]):
            lines.append(f"    {check}: {n}")
        return "\n".join(lines)


def tier_b(graph: Graph, profile: SeriesProfile, policy: RunPolicy, chapters: list[Draft],
           *, client=None) -> ConstraintReport:
    report = ConstraintReport(chapters=len(chapters))
    for draft in chapters:
        report.words += len(words(draft.text))
        card = graph.card(draft.card_id) if draft.card_id else None
        sc = run_checks(CheckContext(draft=draft, graph=graph, profile=profile,
                                     policy=policy, card=card, client=client))
        report.hard += len(sc.hard)
        report.soft += len(sc.soft)
        report.notes += len(sc.notes)
        for m in sc.marginalia:
            if m.severity == "hard":
                report.by_check[m.check] = report.by_check.get(m.check, 0) + 1
    return report


# ------------------------------------------------------------------- recall
@dataclass
class RecallReport:
    """Plot recall, credited only above the contamination baseline."""

    matched: list[str] = field(default_factory=list)
    matched_but_known: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)

    @property
    def credited(self) -> int:
        return len(self.matched)

    def render(self) -> str:
        total = self.credited + len(self.matched_but_known) + len(self.missed)
        return "\n".join([
            "Plot recall (contamination-adjusted)",
            f"  events the real book contains: {total}",
            f"  matched and NOT recalled by the bare model: {self.credited}  ← the only ones that count",
            f"  matched but the bare model already knew them: {len(self.matched_but_known)} (excluded)",
            f"  missed: {len(self.missed)}",
        ])


def plot_recall(real_events: list[str], generated_events: list[str], probe_result: ProbeResult | None) -> RecallReport:
    """Compare event sets by content-word overlap, excluding what the probe knew.

    Set overlap rather than a model judgment on purpose: a model scoring its own
    recall against a book it may have memorised is the exact failure this whole
    section exists to avoid.
    """
    from .textstats import _STOP

    def sig(s: str) -> set[str]:
        return {w.lower() for w in words(s) if len(w) > 3 and w.lower() not in _STOP}

    known = [sig(e) for e in (probe_result.recalled_events if probe_result else [])]
    gen = [sig(e) for e in generated_events]
    report = RecallReport()
    for event in real_events:
        s = sig(event)
        if not s:
            continue
        hit = any(len(s & g) / len(s) >= 0.5 for g in gen)
        was_known = any(len(s & k) / len(s) >= 0.5 for k in known)
        if hit and was_known:
            report.matched_but_known.append(event)
        elif hit:
            report.matched.append(event)
        else:
            report.missed.append(event)
    return report


# ------------------------------------------------------------------- ablations
ABLATIONS: dict[str, dict] = {
    "full": {},
    "no_belief_graph": {"disable_checks": ["epistemic"], "context_drop": ["state_block"]},
    "no_exemplars": {"context_drop": ["exemplars"]},
    "no_repetition_auditor": {"disable_checks": ["repetition"]},
    "half_context": {"context_scale": 0.5},
    "whole_series_in_context": {"whole_corpus": True},
}


def ablation_policy(base: RunPolicy, name: str) -> RunPolicy:
    """A run policy with one part of the engine switched off.

    'Spend fifty dollars finding out whether a checker helps before spending a
    week making it cheaper' — this is the function that makes that cheap.
    """
    import copy

    spec = ABLATIONS.get(name)
    if spec is None:
        raise KeyError(f"unknown ablation {name!r}; try {sorted(ABLATIONS)}")
    policy = copy.deepcopy(base)
    policy.name = f"{base.name}+{name}"
    for check in spec.get("disable_checks", []):
        if check in policy.checks:
            policy.checks[check].severity = "off"  # type: ignore[assignment]
    if (scale := spec.get("context_scale")):
        policy.context_start_tokens = int(policy.context_start_tokens * scale)
        policy.context_max_tokens = int(policy.context_max_tokens * scale)
    return policy


# ---------------------------------------------------------------------- report
@dataclass
class BacktestReport:
    series: str = ""
    hidden_book: str = ""
    probe: ProbeResult | None = None
    shape: ShapeReport | None = None
    constraints: ConstraintReport | None = None
    recall: RecallReport | None = None
    ablation: str = "full"

    def render(self) -> str:
        parts = [f"BACKTEST · {self.series} · hidden book {self.hidden_book} · ablation {self.ablation}", ""]
        for section in (self.probe, self.shape, self.constraints, self.recall):
            if section is not None:
                parts += [section.render(), ""]
        if self.probe and not self.probe.blind:
            parts.append("NOTE: Tier C did not run — the hidden book is contaminated. Tier C is where "
                         "the engine is actually on trial; nothing else here substitutes for it.")
        return "\n".join(parts)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "series": self.series, "hidden_book": self.hidden_book, "ablation": self.ablation,
            "probe": asdict(self.probe) if self.probe else None,
            "shape": asdict(self.shape) if self.shape else None,
            "constraints": asdict(self.constraints) if self.constraints else None,
            "recall": asdict(self.recall) if self.recall else None,
        }
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return p


def hide_book(graph: Graph, book_id: str) -> list[str]:
    """Remove a book from retrieval so the engine must continue without it.

    Returns the scene IDs removed, so the caller can score against them. The
    scenes stay in the file — only the FTS and vector indexes lose them —
    because the harness needs the real book as an answer key.
    """
    ids = [s.scene_id for s in graph.scenes(book=book_id)]
    for sid in ids:
        graph.conn.execute("DELETE FROM scenes_fts WHERE scene_id=?", (sid,))
        graph.conn.execute("DELETE FROM chunks WHERE scene_id=?", (sid,))
        graph.conn.execute("DELETE FROM phrase_ledger WHERE scene_id=?", (sid,))
    graph.conn.execute("UPDATE scenes SET generated=2 WHERE book_id=?", (book_id,))
    graph.set_meta("hidden_book", book_id)
    graph.commit()
    return ids


def real_shape(graph: Graph, book_id: str) -> Skeleton:
    """Measure the hidden book, for Tier A to compare against."""
    return measure_skeleton(graph, book=book_id)

"""Settling the promise ledger: which scene, if any, paid each promise off.

2,842 of 2,886 promises in the real graph are recorded ``open``, across five
*finished* novels that plainly do pay off most of what they set up. Nothing has
ever closed one. Two things break as a result. The thesis ranking divides what
an ending pays by the weight of the whole open ledger, so a denominator inflated
by roughly the entire corpus crushes the evidence term to noise. And the
planner's window onto that ledger fills with setups book three already resolved,
so a continuation is planned around dead material.

Two stages, and the split is the same one entity resolution uses: find the
candidates for nothing, then spend a model call only on the judgment no string
comparison can make.

**This module is the free half.** For each scene, which promises could it
possibly be paying off? Everything here is arithmetic over records already
extracted — no model, no cost, same answer every run.

Three rules were paid for in mistakes and are not negotiable:

*Ask the question per scene, not per pair.* "Does this scene pay this promise"
across every plausible pair is ~260,000 model calls and about $320. "Which of
these promises does this scene pay off", walked over 836 scenes, is 836 calls —
and each scene's text, the expensive part, is sent once instead of seventy
times.

*Never filter a promise down to nothing.* A promise owed by nobody the graph can
resolve — "the narrative", a group, a name that matches no entity — gets every
later scene rather than no scene at all. Filtered strictly it would be silently
never examined, which is indistinguishable from examined-and-unpaid.

*Do not narrow by book, and do not skip by kind.* Both were tried. Confining a
payoff to its plant's own book declares unpayable the 70% of promises owed by
characters who go on appearing, and — because scope is derived from what
survives the pass — a promise planted in book one and paid in book three would
then survive unexamined and be labelled a *series-level* debt for the
continuation to discharge. The filter does not merely miss payoffs; it
manufactures false ones. Skipping ``threat`` and ``prophecy`` is the same
circular guess wearing the extractor's own label.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field

from .db import Graph
from .profile import SeriesProfile
from .textstats import estimate_tokens


@dataclass
class IndexReport:
    """The shape of the work Stage 2 would have to do."""

    scenes: int = 0
    promises: int = 0
    pairs: int = 0
    unanchored: int = 0
    unowned: int = 0
    per_scene: list[int] = field(default_factory=list)
    prompt_tokens: int = 0

    def cost_usd(self, rate_in: float, *, batch: bool = True) -> float:
        usd = self.prompt_tokens / 1_000_000 * rate_in
        return usd * 0.5 if batch else usd

    def render(self) -> str:
        sizes = sorted(self.per_scene)
        med = statistics.median(sizes) if sizes else 0
        p90 = sizes[int(len(sizes) * 0.9)] if sizes else 0
        lines = [
            f"{self.scenes:,} scenes · {self.promises:,} open promises · {self.pairs:,} candidate pairs",
            f"candidates per scene: median {med:.0f}, p90 {p90}, max {max(sizes) if sizes else 0}",
            f"promises with no resolvable plant (every scene is 'after'): {self.unanchored:,}",
            f"promises owed to nobody resolvable (over-included, never dropped): {self.unowned:,}",
            "",
            f"one pass = {self.scenes:,} calls · {self.prompt_tokens:,} input tokens",
        ]
        for model, rate in (("claude-haiku-4-5", 1.0), ("claude-sonnet-5", 2.0), ("claude-opus-5", 5.0)):
            lines.append(f"  {model:20} ${self.cost_usd(rate):>7,.2f} batched")
        return "\n".join(lines)


def _unj(value: str | None) -> list[str]:
    try:
        out = json.loads(value or "[]")
    except ValueError:
        return []
    return [str(x) for x in out if x] if isinstance(out, list) else []


def candidate_index(graph: Graph, profile: SeriesProfile | None = None) -> tuple[dict[str, list[str]], IndexReport]:
    """For each scene, the open promises that scene could be paying off.

    A promise is a candidate for a scene when the scene comes after the promise
    was planted and somebody the promise is owed by is in it. Both halves fall
    back to over-inclusion rather than exclusion: an unresolvable plant makes
    every scene "after", and an unresolvable debtor makes every later scene a
    candidate.
    """
    from .resolve import norm_name

    scenes = [dict(r) for r in graph.conn.execute(
        "SELECT scene_id, ord, cast_json, pov FROM scenes ORDER BY ord")]
    ord_of = {s["scene_id"]: s["ord"] for s in scenes}

    def canon(name: str) -> str:
        return profile.canonical(name) if profile and name else (name or "")

    # Scene cast is now derived from the events cited to each scene, so it is a
    # participation index rather than the capitalised-word heuristic it used to
    # be, and can be intersected against directly.
    cast_of: dict[str, set[str]] = {}
    for s in scenes:
        cast = {canon(c) for c in _unj(s["cast_json"])}
        if s["pov"]:
            cast.add(canon(s["pov"]))
        cast_of[s["scene_id"]] = {c for c in cast if c}

    # Resolve an owed_by token the same way cast entries were resolved, so the
    # two sides of the intersection are in one namespace. Names, ids and
    # aliases all collapse to the canonical name; a form claimed by more than
    # one entity is not guessed between.
    by_form: dict[str, str] = {}
    normed: dict[str, set[str]] = {}
    for r in graph.conn.execute("SELECT entity_id, name, aliases FROM entities"):
        target = canon(r["name"])
        for key in (r["name"], r["entity_id"], *_unj(r["aliases"])):
            if key:
                by_form.setdefault(key.strip().casefold(), target)
                normed.setdefault(norm_name(key), set()).add(target)

    def resolve(token: str) -> str:
        raw = (token or "").strip()
        if not raw:
            return ""
        hit = by_form.get(raw.casefold())
        if hit:
            return hit
        moved = graph.resolve_entity_id(raw)
        if moved != raw and by_form.get(moved.strip().casefold()):
            return by_form[moved.strip().casefold()]
        claimants = normed.get(norm_name(raw), set())
        return next(iter(claimants)) if len(claimants) == 1 else ""

    report = IndexReport(scenes=len(scenes))
    index: dict[str, list[str]] = {s["scene_id"]: [] for s in scenes}

    for p in graph.conn.execute(
            "SELECT promise_id, planted_in, owed_by FROM promises WHERE status='open'"):
        report.promises += 1

        planted = [s for s in _unj(p["planted_in"]) if s in ord_of]
        if planted:
            after = min(ord_of[s] for s in planted)
        else:
            # No usable plant: the promise has lost its place in the timeline,
            # so every scene is potentially after it. Over-include rather than
            # drop — this is the population `bp extract` now pins to prevent.
            after = float("-inf")
            report.unanchored += 1

        owed = {resolve(t) for t in _unj(p["owed_by"])}
        owed.discard("")
        if not owed:
            report.unowned += 1

        for s in scenes:
            if s["ord"] <= after:
                continue
            if owed and not (owed & cast_of[s["scene_id"]]):
                continue
            index[s["scene_id"]].append(p["promise_id"])

    # What Stage 2 would actually send: each scene's text once, plus a line per
    # candidate promise.
    summaries = {r["promise_id"]: r["summary"] or "" for r in graph.conn.execute(
        "SELECT promise_id, summary FROM promises WHERE status='open'")}
    for s in graph.conn.execute("SELECT scene_id, tokens FROM scenes"):
        ids = index.get(s["scene_id"], [])
        report.per_scene.append(len(ids))
        report.pairs += len(ids)
        listing = sum(estimate_tokens(f"{pid}: {summaries.get(pid, '')}") for pid in ids)
        report.prompt_tokens += int(s["tokens"] or 0) + listing

    return index, report

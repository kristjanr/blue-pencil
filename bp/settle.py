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
import re
import statistics
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .db import Graph
from .grounding import _norm, quote_in_scene
from .profile import SeriesProfile
from .textstats import estimate_tokens


#: Billed tokens per settling call, as a function of how many candidate
#: promises that call's listing carries. Two books have now been instrumented
#: end to end, which is what it took to see the shape:
#:
#:     book 1   51 candidates/scene   162 calls    976,483 in   180,308 out
#:     book 2  183 candidates/scene   166 calls  2,420,889 in   367,559 out
#:
#: At sonnet-5's $2/$10 those reproduce the invoices to the cent — $3.76 and
#: $8.52 — so the fit below is against the bill itself, not against an estimate
#: of it.
#:
#: **The estimator is no longer in the cost path, and that is the point.** The
#: old model priced `estimate_tokens(prompt) * a scalar`, which meant every
#: tokenizer quirk had to be absorbed by one constant fitted on one book. That
#: constant read 1.25 against book one and 1.75 against book two: not a
#: constant. Cost is now predicted from candidate density directly, and the
#: estimate is kept only to describe the work.
#:
#: Input is linear in density with a positive intercept, because that is what a
#: call is: a fixed system block plus the scene's prose, then a listing that
#: grows one line per candidate. The intercept is that fixed part.
#:
#: This is the correction that matters for the tail. Fitting the *ratio* of
#: bill to estimate as a power law — the obvious move with two points — makes
#: billed input grow as roughly d^1.15, which has no mechanism behind it: it
#: would require each listing line to cost more tokens as the list gets longer.
#: Extrapolated to book five that difference is about $15.
_BILLED_IN_BASE = 2_722.0          #: system block + scene prose, per call
_BILLED_IN_PER_CANDIDATE = 64.82   #: one listing line, as billed

#: Output is sub-linear: the model writes about the few promises it closes, and
#: closes do not scale with the size of the list it was offered.
#:
#: The exponent is the one number here with independent support. Fitted across
#: the two books it is 0.538; fitted *within* book two alone — 108 candidates
#: producing 1,825 output tokens, 201 producing 2,559 — it is 0.545. Two
#: derivations that share no data agreeing to 1.5% is the only real validation
#: in this module, and it is why the tail projection is worth trusting at all.
#:
#: The old flat 1,113 tokens per call was book one's average read as a
#: constant. It is book five's figure that it gets wrong: at 574 candidates the
#: flat value under-predicts output by a factor of three.
_BILLED_OUT_COEF = 134.0
_BILLED_OUT_EXP = 0.538


def billed_tokens_per_call(candidates: float) -> tuple[float, float]:
    """(input, output) tokens one settling call bills at this listing size.

    Both halves are two-parameter fits through two points, so they reproduce
    those two books exactly and nothing else is validated. Treat a projection
    more than a book or two beyond book five as a shape, not a number, and
    re-fit from `SettleReport.calibration` whenever another book is billed.
    """
    d = max(0.0, float(candidates))
    if not d:
        return _BILLED_IN_BASE, 0.0
    return (_BILLED_IN_BASE + _BILLED_IN_PER_CANDIDATE * d,
            _BILLED_OUT_COEF * d ** _BILLED_OUT_EXP)


@dataclass
class LedgerAudit:
    """Whether every paid promise in the table can prove it was paid.

    A guard on the write path only sees writes that take the write path. Most
    corrections to this ledger have not: the entity merges, the drop-cap
    repair, the 142 restored demotions and the 41 born-paid promises were all
    applied by scripts opening the database directly, and `bp resolve apply` is
    the only one of those that has a command behind it. So this reads what is
    actually stored and asks the three questions a close has to be able to
    answer, whoever wrote it.

    The third is the one worth having. A quote is verified against the scene
    when it is stored, and never again — so a corpus repair that moves the text
    out from under it leaves a close whose evidence no longer exists. The
    drop-cap fix edited the opening line of hundreds of scenes and nobody
    checked. That failure is invisible from every other angle.

    Which is why the pass reports *where the evidence it checked lives*, not
    just how much of it there was. Books 1 and 2 hold 354 of the 358 quotes in
    the ledger; books 3, 4 and 5 hold four between them. A clean result is
    therefore nearly silent about the drop-cap repair, which touched 24 book 5
    scenes — and reading it as clearing that question is this project's own bug
    in its most flattering costume, a green check taken as evidence about a
    case it never covered. The count alone invites that reading. The
    distribution makes it obvious.
    """

    paid: int = 0
    #: book -> paid promises whose evidence sits in that book. Coverage, not a
    #: statistic: it is what says which question a pass has actually answered.
    by_book: dict[str, int] = field(default_factory=dict)
    no_scene: list[str] = field(default_factory=list)
    scene_missing: list[tuple[str, str]] = field(default_factory=list)
    no_quote: list[str] = field(default_factory=list)
    quote_not_in_scene: list[tuple[str, str]] = field(default_factory=list)
    no_trail: list[str] = field(default_factory=list)

    @property
    def failures(self) -> int:
        return (len(self.no_scene) + len(self.scene_missing) + len(self.no_quote)
                + len(self.quote_not_in_scene) + len(self.no_trail))

    def render(self) -> str:
        # A ledger with nothing paid in it passes every clause above without
        # examining anything, and reporting that as clean is the bug this
        # project keeps finding. Say what was checked, not just what failed.
        if not self.paid:
            return "ledger audit: no paid promises to check — this is not a pass"
        if not self.failures:
            return (f"ledger audit: {self.paid:,} paid promises, all three invariants hold"
                    + self._coverage())
        lines = [f"LEDGER AUDIT FAILED: {self.failures} of {self.paid:,} paid promises"]
        for label, bad in (("no paid_in", self.no_scene),
                           ("paid_in names no scene", self.scene_missing),
                           ("no paid_quote", self.no_quote),
                           ("quote is not in the scene it names", self.quote_not_in_scene),
                           ("no record_changes trail", self.no_trail)):
            if bad:
                shown = [b[0] if isinstance(b, tuple) else b for b in bad[:5]]
                lines.append(f"  {label}: {len(bad)} ({', '.join(shown)})")
        return "\n".join(lines) + self._coverage()

    def _coverage(self) -> str:
        """Which books the checked evidence sits in — what the pass covers.

        A lopsided distribution is the interesting case, so it is called out
        rather than left to be noticed: a book holding almost no quotes has not
        been audited in any useful sense, whatever the total says.
        """
        if not self.by_book:
            return ""
        counts = sorted(self.by_book.items())
        line = "\n  evidence checked sits in: " + ", ".join(f"{b} {n}" for b, n in counts)
        thin = [b for b, n in counts if n < max(5, self.paid // 20)]
        if thin and len(thin) < len(counts):
            line += (f"\n  — a pass says little about {', '.join(thin)}: "
                     f"almost no evidence there to check")
        return line


def audit_paid_promises(graph: Graph) -> LedgerAudit:
    """Read the ledger and check that every close can still prove itself."""
    audit = LedgerAudit()
    rows = graph.conn.execute(
        "SELECT promise_id, paid_in, paid_quote FROM promises WHERE status='paid'").fetchall()
    audit.paid = len(rows)
    if not rows:
        return audit

    wanted = {r["paid_in"] for r in rows if r["paid_in"]}
    scenes = {}
    if wanted:
        qs = ",".join("?" * len(wanted))
        scenes = {r["scene_id"]: r["text"] for r in graph.conn.execute(
            f"SELECT scene_id, text FROM scenes WHERE scene_id IN ({qs})", tuple(wanted))}
    normed = {sid: _norm(text) for sid, text in scenes.items()}
    book_of = {r["scene_id"]: r["book_id"] for r in graph.conn.execute(
        "SELECT scene_id, book_id FROM scenes")}
    with_trail = {r[0] for r in graph.conn.execute(
        "SELECT DISTINCT record_id FROM record_changes WHERE table_name='promises'")}

    for row in rows:
        pid, sid, quote = row["promise_id"], row["paid_in"], row["paid_quote"] or ""
        if (book := book_of.get(sid)):
            audit.by_book[book] = audit.by_book.get(book, 0) + 1
        if pid not in with_trail:
            audit.no_trail.append(pid)
        if not sid:
            audit.no_scene.append(pid)
            continue
        if sid not in scenes:
            audit.scene_missing.append((pid, sid))
            continue
        if not quote:
            audit.no_quote.append(pid)
        elif not quote_in_scene(quote, normed[sid], scenes[sid]):
            audit.quote_not_in_scene.append((pid, sid))
    return audit


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
    #: book -> (scenes, candidate pairs, prompt tokens). Promises accumulate as
    #: a series runs, so the last book's scenes carry several times the
    #: candidates of the first's. A full-run cost projected by multiplying book
    #: one is wrong by a large factor, in the direction that gets a budget
    #: approved and then overrun.
    by_book: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    #: The judge model this pass would really use. Empty means "not told", and
    #: the per-book table then says so rather than quietly pricing at sonnet.
    model: str = ""
    #: The state of what has already been settled. Shown here because this is
    #: the command run before deciding to spend on the next book, and a listing
    #: built over a ledger whose closes cannot prove themselves is costing money
    #: to extend a bad record.
    audit: LedgerAudit = field(default_factory=LedgerAudit)

    def cost_usd(self, rate_in: float, rate_out: float | None = None,
                 *, batch: bool = True, calls: int | None = None) -> float:
        """What a pass would actually cost, not what the token estimate says.

        Two terms, because the bill has two. Output was previously folded into
        a single scalar on input, which hid it: output bills at five times
        input on every model in the table, so a settling run that returns a
        verbatim quote and a sentence of reasoning per close pays a rate the
        estimate never named. Folding it into the input term also meant the
        correction silently moved whenever the *shape* of the answer changed,
        which is not something a cost model should do quietly.

        Priced per book and summed, never from the corpus average. Output is
        concave in candidate density, so averaging 49 candidates/scene and 574
        into one figure of 300 and pricing that once over-quotes — the safe
        direction, but a different number from the one the per-book table
        prints, and the two should not silently disagree.

        `rate_out` is optional only so old callers keep working; pass it.
        """
        # book -> (calls, candidates per scene); the whole report is one
        # unlabelled slice when nothing has told us the book boundaries.
        slices = ([(n, pairs / n if n else 0) for n, pairs, _ in self.by_book.values()]
                  if self.by_book else
                  [(self.scenes, self.pairs / self.scenes if self.scenes else 0)])
        if calls is not None:                     # pricing a subset of one shape
            density = slices[0][1] if len(slices) == 1 else self.pairs / max(self.scenes, 1)
            slices = [(calls, density)]

        usd = 0.0
        for n, density in slices:
            tok_in, tok_out = billed_tokens_per_call(density)
            usd += n * tok_in / 1_000_000 * rate_in
            if rate_out is not None:
                usd += n * tok_out / 1_000_000 * rate_out
        return usd * 0.5 if batch else usd

    def render(self) -> str:
        from .llm import rate_for

        sizes = sorted(self.per_scene)
        med = statistics.median(sizes) if sizes else 0
        p90 = sizes[int(len(sizes) * 0.9)] if sizes else 0
        lines = [
            f"{self.scenes:,} scenes · {self.promises:,} open promises · {self.pairs:,} candidate pairs",
            f"candidates per scene: median {med:.0f}, p90 {p90}, max {max(sizes) if sizes else 0}",
            f"promises with no resolvable plant (every scene is 'after'): {self.unanchored:,}",
            f"promises owed to nobody resolvable (over-included, never dropped): {self.unowned:,}",
            self.audit.render(),
            "",
            f"one pass = {self.scenes:,} calls · {self.prompt_tokens:,} estimated input tokens "
            f"(a description of the work; the price below does not use it)",
            "priced from candidate density against two instrumented books, "
            "per book and summed — books 1 and 2 reproduce to the cent, the "
            "rest is extrapolation:",
        ]
        for model in ("claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"):
            r_in, r_out = rate_for(model)
            lines.append(f"  {model:20} ${self.cost_usd(r_in, r_out):>7,.2f} batched  "
                         f"${self.cost_usd(r_in, r_out, batch=False):>7,.2f} live")
        if self.by_book:
            judge = self.model or "claude-sonnet-5"
            r_in, r_out = rate_for(judge)
            lines += ["", f"per book ({judge}, live) — promises accumulate, "
                          "so these are not equal:"]
            for book, (n, pairs, _tokens) in sorted(self.by_book.items()):
                per = pairs / n if n else 0
                tok_in, tok_out = billed_tokens_per_call(per)
                usd = n * (tok_in / 1e6 * r_in + tok_out / 1e6 * r_out)
                lines.append(f"  {book:12} {n:4} scenes · {per:6.0f} candidates/scene · "
                             f"${usd:>6,.2f}")
        return "\n".join(lines)


def _unj(value: str | None) -> list[str]:
    try:
        out = json.loads(value or "[]")
    except ValueError:
        return []
    return [str(x) for x in out if x] if isinstance(out, list) else []


def candidate_index(graph: Graph, profile: SeriesProfile | None = None,
                    *, exclude: set[str] | None = None) -> tuple[dict[str, list[str]], IndexReport]:
    """For each scene, the open promises that scene could be paying off.

    ``exclude`` drops promises from every listing — what a settling run already
    closed. Passing the closes from book 1 back in is how the saving from
    settling in reading order gets measured before it is paid for: a promise
    closed in book 1 stops appearing in every later scene, and the listing is
    96% of what a run costs.

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

    report = IndexReport(scenes=len(scenes), audit=audit_paid_promises(graph))
    index: dict[str, list[str]] = {s["scene_id"]: [] for s in scenes}

    skip = exclude or set()
    for p in graph.conn.execute(
            "SELECT promise_id, planted_in, owed_by FROM promises WHERE status='open'"):
        if p["promise_id"] in skip:
            continue
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
        "SELECT promise_id, summary FROM promises WHERE status='open'")
        if r["promise_id"] not in skip}
    for s in graph.conn.execute("SELECT scene_id, book_id, tokens FROM scenes"):
        ids = index.get(s["scene_id"], [])
        report.per_scene.append(len(ids))
        report.pairs += len(ids)
        listing = sum(estimate_tokens(f"{pid}: {summaries.get(pid, '')}") for pid in ids)
        cost = int(s["tokens"] or 0) + listing
        report.prompt_tokens += cost
        n, pairs, tokens = report.by_book.get(s["book_id"], (0, 0, 0))
        report.by_book[s["book_id"]] = (n + 1, pairs + len(ids), tokens + cost)

    return index, report


def _record_spend(report: "SettleReport", usage: Usage, model: str) -> None:
    """Copy the API's own usage record onto the report, so it outlives the run."""
    report.model = model
    report.calls = usage.calls
    report.input_tokens = usage.input_tokens
    report.output_tokens = usage.output_tokens
    report.cache_read_tokens = usage.cache_read_tokens
    report.cache_write_tokens = usage.cache_write_tokens


# ------------------------------------------------------------------- stage two
SETTLE_SYSTEM = """You decide which of a novel's open promises a given scene pays off.

A promise is a setup the text has made: a prophecy, a vow, a foreshadowing beat, a
dangling question. It is PAID when the text delivers the thing that was set up — the
log arrives, the vault opens, the debt is called in.

You are given one scene and a list of promises planted before it. Almost all of them
are unrelated to this scene. Say so by leaving them out.

Close a promise ONLY when this scene's own words show it being paid off, and quote the
line that does it. The quote must be copied exactly from the scene text you were given.
If you cannot point at a line, the answer is no.

Two things to hold on to, because they pull in opposite directions:

A payoff almost never reuses the setup's vocabulary. The setup says "Cyra's log will
reach Sol one day"; the payoff says "the packet finally decoded, eleven years late".
Do not require shared wording, and do not go looking for it.

Sharing a subject is not being paid. A scene that mentions the vault, worries about the
vault, or moves the vault has not opened it. Continuing a thread is not discharging it.
If the promise could still be paid off later, it is not paid here.

Prefer leaving a promise open. An unpaid promise left open costs a little accuracy in a
ledger; a promise wrongly closed erases something the book still owes and leaves no
trace that it did."""


class _Close(BaseModel):
    promise_id: str = Field(description="id from the candidate list, copied exactly")
    quote: str = Field(description="the line in THIS scene that pays it off, verbatim")
    why: str = Field(default="", description="one sentence: what was owed, and how this pays it")
    confidence: float = Field(default=0.5)


class _Settlement(BaseModel):
    closes: list[_Close] = []


@dataclass
class SettleReport:
    """What a settling run found, and what it cost."""

    scenes_read: int = 0
    #: scene, promise, why, confidence, and the quote that proved it. The
    #: quote is the evidence; a close recorded without it cannot be audited
    #: by anyone, including the next run.
    closes: list[tuple[str, str, str, float, str]] = field(default_factory=list)
    rejected_quote: list[tuple[str, str, str]] = field(default_factory=list)
    rejected_unknown: list[tuple[str, str]] = field(default_factory=list)
    echoed: int = 0
    unechoed: int = 0
    usd: float = 0.0
    stopped: str = ""
    errors: list[str] = field(default_factory=list)
    #: What the run actually consumed, straight off the API's own usage
    #: records. Book one cost $4.71 against an estimate of $3.58 and the gap
    #: could not be explained, because the only number kept was the dollar
    #: total — which is a product of six terms and so tells you nothing about
    #: any of them. An estimator can only be corrected against the thing it
    #: estimates, so a run that does not record its own tokens condemns the
    #: next estimate to the same error.
    model: str = ""
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    #: What `candidate_index` predicted for exactly the scenes this run read,
    #: so the comparison is like-for-like rather than against a whole-corpus
    #: figure that included scenes the run skipped.
    estimated_input_tokens: int = 0
    #: (candidates offered, output tokens emitted) per call. Output is half the
    #: bill and the cost model treats it as flat per call, fitted to book one at
    #: 91 candidates per scene. Book five averages 675. If output instead grows
    #: with the listing, every later book costs more than projected and the
    #: error compounds in the direction that overruns a budget. One run cannot
    #: tell the two apart; this makes the next one able to.
    output_by_candidates: list[tuple[int, int]] = field(default_factory=list)

    def double_closed(self) -> list[tuple[str, list[str]]]:
        """Promises that more than one scene claimed to pay off.

        Empty for a live run by construction — it drops a promise from every
        later listing as soon as something closes it — and so this is also the
        price of not doing that: each entry is a promise that stayed in the
        listing of every scene after its payoff, at 64.82 billed input tokens
        per scene per promise. The batch path pays that and gets ambiguity back.
        """
        by_promise: dict[str, list[str]] = {}
        for sid, pid, *_rest in self.closes:
            by_promise.setdefault(pid, []).append(sid)
        return sorted((p, sorted(set(s))) for p, s in by_promise.items()
                      if len(set(s)) > 1)

    def output_scaling(self) -> str:
        """Does output grow with the candidate list, or is it flat per call?"""
        pts = [p for p in self.output_by_candidates if p[0] > 0]
        if len(pts) < 8:
            return ""
        pts.sort()
        half = len(pts) // 2
        lo = statistics.mean(o for _, o in pts[:half])
        hi = statistics.mean(o for _, o in pts[-half:])
        lo_c = statistics.mean(c for c, _ in pts[:half])
        hi_c = statistics.mean(c for c, _ in pts[-half:])
        return (f"output vs candidates: {lo_c:.0f} candidates -> {lo:,.0f} output tokens, "
                f"{hi_c:.0f} -> {hi:,.0f} ({hi / max(1.0, lo):.2f}x for "
                f"{hi_c / max(1.0, lo_c):.1f}x the listing)")

    def calibration(self) -> str:
        """How far the estimate ran from the bill, term by term."""
        from .llm import rate_for

        if not self.calls or not self.estimated_input_tokens:
            return ""
        billed_in = self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        ratio = billed_in / self.estimated_input_tokens
        out_share = 0.0
        if billed_in or self.output_tokens:
            rate_in, rate_out = rate_for(self.model)
            out_share = (self.output_tokens * rate_out) / max(
                1e-9, billed_in * rate_in + self.output_tokens * rate_out)
        return (f"estimate vs bill: {self.estimated_input_tokens:,} predicted input "
                f"vs {billed_in:,} billed ({ratio:.2f}x) · "
                f"{self.output_tokens:,} output tokens = {out_share:.0%} of spend · "
                f"${self.usd / self.calls:.4f} per call")

    def as_dict(self) -> dict:
        """The whole result, not the first 25 of it.

        `render` truncates for reading; comparing two runs needs every close,
        and a console tail is not a record. Learned by compromising exactly
        that comparison.
        """
        return {
            "scenes_read": self.scenes_read,
            "usd": round(self.usd, 4),
            "stopped": self.stopped,
            "spend": {
                "model": self.model,
                "calls": self.calls,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cache_read_tokens": self.cache_read_tokens,
                "cache_write_tokens": self.cache_write_tokens,
                "estimated_input_tokens": self.estimated_input_tokens,
            },
            "closes": [{"scene": s, "promise_id": p, "why": w, "confidence": c,
                        "quote": q}
                       for s, p, w, c, q in self.closes],
            "rejected_quote": [{"scene": s, "promise_id": p, "quote": q}
                               for s, p, q in self.rejected_quote],
            "rejected_unknown": [{"scene": s, "promise_id": p} for s, p in self.rejected_unknown],
            "echoed": self.echoed,
            "unechoed": self.unechoed,
            "double_closed": [{"promise_id": p, "scenes": s} for p, s in self.double_closed()],
            "errors": self.errors,
        }

    def render(self) -> str:
        lines = [
            f"{self.scenes_read} scenes read · {len(self.closes)} promise(s) closed · ${self.usd:,.2f}",
            f"closes rejected because the quote is not in the scene: {len(self.rejected_quote)}",
            f"closes naming a promise that was not a candidate: {len(self.rejected_unknown)}",
        ]
        if (dup := self.double_closed()):
            lines.append(
                f"{len(dup)} promise(s) closed by more than one scene; the earliest was kept "
                f"(e.g. {dup[0][0]} in {', '.join(dup[0][1][:3])})")
        if (cal := self.calibration()):
            lines.append(cal)
        if (scale := self.output_scaling()):
            lines.append(scale)
        # The rejected quote is the interesting one: a model that paraphrases
        # instead of copying looks identical to one that invents, and only the
        # text tells them apart.
        for sid, pid, quote in self.rejected_quote[:5]:
            lines.append(f"  REJECTED {sid} <- {pid}: {quote[:100]!r}")
        if (total := self.echoed + self.unechoed):
            # work-ed's check: if it only ever closes promises whose own words
            # are echoed in the scene, it is doing string matching in an
            # expensive costume rather than reading.
            lines.append(
                f"of the closes, {self.unechoed} ({self.unechoed / total:.0%}) were on scenes that do "
                f"NOT echo the promise's own distinctive words")
        lines += [f"  {sid}  <-  {pid}  ({conf:.2f}) {why[:70]}"
                  for sid, pid, why, conf, _q in self.closes[:25]]
        if self.stopped:
            lines.append(f"STOPPED: {self.stopped}")
        lines += [f"error: {e}" for e in self.errors[:8]]
        return "\n".join(lines)


def _scene_ord(graph, scene_id: str) -> int | None:
    row = graph.conn.execute("SELECT ord FROM scenes WHERE scene_id=?",
                             (scene_id,)).fetchone()
    return None if row is None else row["ord"]


def _close(graph, promise_id: str, scene_id: str, run_id: str, confidence: float,
           quote: str = "") -> str:
    """Mark a promise paid, leaving behind what it was and which run did it.

    Closing overwrites `status` and `paid_in` on a record that was open, and we
    will correct this settler's prompt at least once. Without the trail there is
    no way to ask "which closes came from the run before the fix" — the same
    hole that made 156 wrong demotions an archaeology exercise.

    Returns the scene that already held the close, or "" if this one was
    written. The earliest payoff is the right one, and only the live path gets
    that for free: it walks in reading order and drops a promise from every
    later listing the moment something closes it, so it never sees the second
    answer. A batch is offered every scene at once and returns them in whatever
    order it likes — `poll_batch` guarantees none — so two scenes can both close
    one promise and the record ends up naming an arbitrary one of them. Nothing
    in the run looks wrong afterwards: both closes carry a verified quote.

    Hence: never overwrite a close this one cannot be shown to beat. If either
    scene has no `ord` the comparison cannot be made, so the incumbent stands.
    Re-running the live path over settled scenes hits the same guard, which it
    previously did not.
    """
    before = graph.conn.execute(
        "SELECT status, paid_in, paid_quote FROM promises WHERE promise_id=?",
        (promise_id,)).fetchone()
    if before is None:
        return ""
    if before["status"] == "paid" and before["paid_in"] and before["paid_in"] != scene_id:
        held, incoming = _scene_ord(graph, before["paid_in"]), _scene_ord(graph, scene_id)
        if held is None or incoming is None or held <= incoming:
            return before["paid_in"]
    for fieldname, new in (("status", "paid"), ("paid_in", scene_id), ("paid_quote", quote)):
        if before[fieldname] != new:
            graph.record_change(table="promises", record_id=promise_id, field=fieldname,
                                old=before[fieldname], new=new, run_id=run_id,
                                reason=f"settled at confidence {confidence:.2f}")
    graph.conn.execute(
        "UPDATE promises SET status='paid', paid_in=?, paid_quote=? WHERE promise_id=?",
        (scene_id, quote, promise_id))
    return ""


def _distinctive(text: str) -> set[str]:
    from .grounding import _STOP, _WORD

    return {w for w in (x.lower() for x in _WORD.findall(text)) if w not in _STOP and len(w) > 4}


def settle(
    graph: Graph, profile: SeriesProfile, client, *, model: str,
    scene_ids: list[str] | None = None, max_usd: float = 1.0,
    apply: bool = False, run_id: str = "", progress=lambda _s: None,
) -> SettleReport:
    """Ask each scene which of its candidate promises it pays off.

    Walks scenes in reading order and drops a promise from every later scene's
    list the moment something closes it. A promise only needs paying once, and
    the earliest payoff is the right one — so this is free accuracy as well as
    the only lossless way to shrink the candidate listing, which is 96% of what
    a run costs.

    Two guards, both cheap. A close whose quote is not literally in the scene
    is rejected here rather than trusted: the same exact test `bp ground` uses,
    and the same defect it found 391 of. And a close naming a promise that was
    not on that scene's list is rejected outright.

    Dry by default. `max_usd` is enforced in this loop because the run does not
    go through `extract`, where the existing cap lives.
    """
    from .llm import Usage, rate_for, structured

    run_id = run_id or f"settle-{time.strftime('%Y%m%d-%H%M%S')}-{model}"
    index, _ = candidate_index(graph, profile)
    rows = {r["promise_id"]: dict(r) for r in graph.conn.execute(
        "SELECT promise_id, summary FROM promises WHERE status='open'")}

    scenes = [dict(r) for r in graph.conn.execute(
        "SELECT scene_id, text, tokens FROM scenes ORDER BY ord")]
    if scene_ids is not None:
        keep = set(scene_ids)
        scenes = [s for s in scenes if s["scene_id"] in keep]

    report = SettleReport()
    usage = Usage()
    closed: set[str] = set()

    for scene in scenes:
        candidates = [p for p in index.get(scene["scene_id"], []) if p not in closed]
        if not candidates:
            continue
        rate_in, rate_out = rate_for(model)
        listing = "\n".join(f"- {pid}: {rows[pid]['summary']}" for pid in candidates if pid in rows)
        # Priced from this call's own listing size rather than a corpus
        # average. The cap guards the next call, so the density that matters is
        # this one's — a late scene in book five carries ten times the
        # candidates of an early one in book one, and `usage.usd` before it is
        # real money already spent.
        tok_in, tok_out = billed_tokens_per_call(len(candidates))
        next_call = tok_in / 1e6 * rate_in + tok_out / 1e6 * rate_out
        projected = usage.usd + next_call
        if projected > max_usd:
            report.stopped = (f"spend cap ${max_usd:,.2f} would be exceeded "
                              f"(${usage.usd:,.2f} spent, {report.scenes_read} scenes read)")
            break

        prompt = (f"--- SCENE {scene['scene_id']} ---\n{scene['text']}\n\n"
                  f"--- PROMISES PLANTED BEFORE THIS SCENE ---\n{listing}")
        # Counted here rather than taken from the index, because this is the
        # string that is actually sent — system prompt and schema included.
        report.estimated_input_tokens += estimate_tokens(SETTLE_SYSTEM) + estimate_tokens(prompt)
        before_out = usage.output_tokens
        try:
            out = structured(client, model, _Settlement, system=SETTLE_SYSTEM, prompt=prompt,
                             max_tokens=4_000, usage=usage, stage="settle")
        except Exception as exc:
            report.errors.append(f"{scene['scene_id']}: {type(exc).__name__}: {exc}")
            continue
        report.scenes_read += 1
        report.output_by_candidates.append((len(candidates), usage.output_tokens - before_out))

        scene_norm = _norm(scene["text"])
        for c in out.closes:
            if c.promise_id not in set(candidates):
                report.rejected_unknown.append((scene["scene_id"], c.promise_id))
                continue
            if not quote_in_scene(c.quote, scene_norm, scene["text"]):
                report.rejected_quote.append((scene["scene_id"], c.promise_id, c.quote))
                continue
            closed.add(c.promise_id)
            report.closes.append((scene["scene_id"], c.promise_id, c.why, c.confidence, c.quote))
            shared = _distinctive(rows[c.promise_id]["summary"]) & _distinctive(scene["text"])
            if shared:
                report.echoed += 1
            else:
                report.unechoed += 1
            if apply:
                _close(graph, c.promise_id, scene["scene_id"], run_id, c.confidence, c.quote)
        progress(f"  {scene['scene_id']}: {len(candidates)} candidates, "
                 f"{len(out.closes)} proposed, ${usage.usd:,.2f} so far")

    report.usd = usage.usd
    _record_spend(report, usage, model)
    if apply:
        graph.conn.commit()
    return report


def settle_batch(
    graph: Graph, profile: SeriesProfile, client, *, model: str,
    scene_ids: list[str] | None = None, max_usd: float = 10.0,
    apply: bool = False, run_id: str = "", progress=lambda _s: None,
) -> SettleReport:
    """The same question, submitted through the Batch API at half price.

    One trade is unavoidable here. Batching submits every scene at once, so a
    promise closed in scene 3 is still offered to scene 40 — the drop-as-closed
    saving does not apply within a batch. What it costs in tokens it more than
    returns in price, and the saving it forgoes can be measured after the fact
    from the closes themselves rather than paid for to discover.

    Estimated before submitting and refused if it would exceed ``max_usd``,
    because a batch cannot be stopped halfway once it is running.
    """
    from .extract import _custom_id
    from .llm import BATCH_DISCOUNT, Usage, poll_batch, rate_for, submit_batch

    run_id = run_id or f"settle-{time.strftime('%Y%m%d-%H%M%S')}-{model}-batch"
    index, _ = candidate_index(graph, profile)
    rows = {r["promise_id"]: dict(r) for r in graph.conn.execute(
        "SELECT promise_id, summary FROM promises WHERE status='open'")}
    scenes = [dict(r) for r in graph.conn.execute(
        "SELECT scene_id, text, tokens FROM scenes ORDER BY ord")]
    if scene_ids is not None:
        keep = set(scene_ids)
        scenes = [s for s in scenes if s["scene_id"] in keep]

    schema = _Settlement.model_json_schema()
    tool = {"name": "emit", "description": "Emit a Settlement.", "input_schema": schema}
    requests, prompts = [], {}
    for s in scenes:
        cands = [p for p in index.get(s["scene_id"], []) if p in rows]
        if not cands:
            continue
        listing = "\n".join(f"- {p}: {rows[p]['summary']}" for p in cands)
        prompt = (f"--- SCENE {s['scene_id']} ---\n{s['text']}\n\n"
                  f"--- PROMISES PLANTED BEFORE THIS SCENE ---\n{listing}")
        cid = _custom_id("settle", s["scene_id"])
        prompts[cid] = (s, set(cands))
        requests.append({
            "custom_id": cid,
            "params": {
                "model": model, "max_tokens": 4_000,
                "system": [{"type": "text", "text": SETTLE_SYSTEM}],
                "tools": [tool], "tool_choice": {"type": "tool", "name": "emit"},
                "messages": [{"role": "user", "content": prompt}],
            },
        })

    report = SettleReport()
    if len(prompts) != len(requests):
        raise ValueError("two scene ids collide as one batch custom_id")

    rate_in, rate_out = rate_for(model)
    # Every term the bill has: the prompt, the system block that rides along
    # with each request, and the answer. The system block was missing here and
    # the answer was missing everywhere — and this is the estimate that decides
    # whether to submit something that cannot afterwards be stopped, so an
    # optimistic one is not a cap but a rubber stamp.
    est_in = sum(estimate_tokens(r["params"]["messages"][0]["content"])
                 for r in requests) + len(requests) * estimate_tokens(SETTLE_SYSTEM)
    report.estimated_input_tokens = est_in
    # Summed call by call at each one's real listing size, because output is
    # sub-linear in it: pricing the batch at its mean density is cheaper than
    # the batch actually is, and a batch cannot be stopped once submitted.
    estimate = BATCH_DISCOUNT * sum(
        t_in / 1e6 * rate_in + t_out / 1e6 * rate_out
        for t_in, t_out in (billed_tokens_per_call(len(c)) for _s, c in prompts.values()))
    progress(f"  {len(requests)} scenes · estimated ${estimate:,.2f} batched")
    if estimate > max_usd:
        report.stopped = (f"estimated ${estimate:,.2f} exceeds the ${max_usd:,.2f} cap; "
                          f"a batch cannot be stopped once submitted, so it was not sent")
        return report

    batch_id = submit_batch(client, requests)
    progress(f"  batch {batch_id} submitted; polling")
    usage = Usage()
    for result in poll_batch(client, batch_id):
        entry = prompts.get(result.custom_id)
        if entry is None:
            continue
        scene, allowed = entry
        if result.result.type != "succeeded":
            report.errors.append(f"{scene['scene_id']}: {result.result.type}")
            continue
        msg = result.result.message
        usage.add(model, msg.usage, stage="settle", batch=True)
        report.scenes_read += 1
        payload = next((b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        if payload is None:
            continue
        try:
            out = _Settlement.model_validate(payload)
        except Exception as exc:
            report.errors.append(f"{scene['scene_id']}: {type(exc).__name__}: {exc}")
            continue

        scene_norm = _norm(scene["text"])
        for c in out.closes:
            if c.promise_id not in allowed:
                report.rejected_unknown.append((scene["scene_id"], c.promise_id))
                continue
            if not quote_in_scene(c.quote, scene_norm, scene["text"]):
                report.rejected_quote.append((scene["scene_id"], c.promise_id, c.quote))
                continue
            report.closes.append((scene["scene_id"], c.promise_id, c.why, c.confidence, c.quote))
            shared = _distinctive(rows[c.promise_id]["summary"]) & _distinctive(scene["text"])
            if shared:
                report.echoed += 1
            else:
                report.unechoed += 1
            if apply:
                _close(graph, c.promise_id, scene["scene_id"], run_id, c.confidence, c.quote)

    report.usd = usage.usd
    _record_spend(report, usage, model)
    if apply:
        graph.conn.commit()
    return report

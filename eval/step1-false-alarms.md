# Step 1 — false-alarm rate, first run

**Date:** 2026-09-16 · **Status:** dry run, not the final number.

The real book 6 is canon. It cannot contradict books 1–5. So every *hard* finding
the checkers raise against it is a checker bug, and counting them measures the
checkers rather than the prose. Repo target: **≤ 2**.

## Setup

- 71 chapters split from `bookz/…(Bobiverse 06) - The Infinite Extent (transcript).md`
  into `eval/book6/ch*.md`. Front matter carries **only what the chapter header
  itself states** — narrator, in-world date, place (67 / 57 / 66 of 71). Inventing
  any of it would plant or suppress findings.
- Checked against the books 1–5 graph with `bp check --json`, no `--llm`, no cards.
- **Caveat:** this transcript came from the *first* (6h56m) capture, with known
  open items. The second capture is not transcribed yet. Treat the count as
  diagnostic, not final.

## Result

```
168 hard · 378 soft · 533 notes        target: ≤ 2 hard
26 of 71 chapters clean

hard by check:   epistemic 161 (96%)
                 objects     4
                 geography   3
```

168 findings, but **not 168 problems**. Three root causes, and one of them is
essentially all of it.

## Root cause 1 — the epistemic check reports ignorance as violation (161 of 168)

Every one of the 161 says the same thing: *"…but there is no recorded information
path to any of them."*

`epistemic.py:_violation` emits `severity="hard"` down both branches. One branch
has positively computed an arrival date later than the chapter — a real finding.
The other has simply **failed to find any path at all**, and says so in the text of
its own message. Both come out hard.

Measured on this run: of the 161, the number that computed an arrival date is
**zero**. Every single hard finding is the no-path branch. The check never once
demonstrated that information could not arrive in time; it reported 161 times that
it could not trace how it did.

The events blamed span all five books, including book 1 — facts every character has
had two centuries to learn. There is no path in the graph because the graph records
almost no propagation chains, not because the knowledge is impossible.

**Fix:** when no path is found, that is *unknown*, not *false*. Severity should be
`note`, the same as the existing "cannot decide" branch immediately above it, which
already makes exactly this distinction for undated events. One line.

**After that fix the count is 7.**

## Root cause 2 — the knower list is not filtered by entity kind

62% of the entities named as "knowers" are not people: `federation`, `earth`,
`nemesis`, `scut_network`, `quinlan-language`, `vr`, `suddar`, `82_eridani`.
107 distinct "characters", of which perhaps 20 can know anything. A language cannot
have an information path.

This alone voids only 2 findings — most name a real character somewhere in the list
— so it is a legibility bug rather than a correctness one. But it makes every
message harder to judge, and it is the same defect as the `objects` findings below.

Two of the 4 `objects` hard findings are "**the Pav** is recorded dead but acts in
this chapter". `the_pav` is `kind=faction` — a species, not a person who can be
restored from backup. The dead-and-acting rule needs the same kind filter.

## Root cause 3 — the travel model predates wormholes

```
Bob-1 was at Ragnarök on 2345-08-01 and is at New Pav here (2347-09-30).
That journey needs 16,391 days; only 790 are available.
```

Two compounding problems. The profile declares a SCUT channel but nothing for
wormholes, which book 5 *introduces* — so a journey the series makes instantaneous
is priced at sublight. And every character's last known position is the end of
book 5, two years stale, because nothing updates placement across a book boundary
the graph does not span.

The second half of that is inherent to the backtest and will not occur when
checking book 7 against a current graph. The first half is real.

## What this run says

The target of ≤ 2 is a long way off at 168 — but the checkers are not 168 kinds of
wrong. They are **three kinds of wrong, one of which accounts for 96%** and is a
single-line severity change.

It is also the fifth instance of this project's recurring pattern: *a deterministic
rule treating absence of evidence as evidence of absence.* The same shape as the
144 phantom contradictions (uncited second reading read as contradiction), the 391
grounding failures, the merge tool reporting an empty database as a finished job,
and `cast_json` treating a mention as a presence. The deterministic core is sound;
what keeps failing is the step where it decides what silence means.

## Next

1. Severity fix, then re-run — expect 7.
2. Kind filter on knowers and on dead-and-acting.
3. Decide whether wormholes belong in the profile's channels.
4. Re-run against the better transcript when it exists, for the number of record.

---

# Second run — 2026-09-16, after the epistemic and kind fixes

```
4 hard · 378 soft · 694 notes        target: ≤ 2 hard
67 of 71 chapters clean              (was 26 of 71)

epistemic 161 → 0
objects     4 → 1
geography   3 → 3
```

**168 → 4.** The epistemic fix removed 161; the `kind=faction` fix removed the
three "the Pav is recorded dead but acts" findings.

A note on how that fix landed, because the first version was mine and it was
wrong. I proposed downgrading the no-path branch to a note unconditionally.
`blue-pencil-3c` measured it against the planted-error fixture first and found it
cost two real catches — in a *built* world, where every observer and report is
recorded on purpose, a missing path really is missing. The distinction isn't the
branch, it's whether the corpus records propagation exhaustively. That is now
declared (`epistemic: {paths_are_complete: …}`, **default off**) rather than
assumed. Off is the assumption that fails safe.

My own recall gate had not noticed the regression, because it matched on check
name and line window and ignored severity — a finding could decay from hard to
note and still count as caught. Fixed, and measured: with completeness off,
acted-on recall is 18/20 and the threshold is ≥ 18, so **it would still have
passed**. The threshold has exactly as much slack as the regression costs. So the
gate now also asserts that a path-complete world produces no note-only catches.

## The four that remain — both are the same rule again

**3 × geography.** The data and the arithmetic are both correct: Ragnarök resolves
to Epsilon Eridani, New Pav to Delta Pavonis, ~22 ly apart, and at the profile's
0.5c that is ~16,400 days. The gap is that **book 5 introduces wormholes** and the
space model has no concept of a transit shortcut — only `information` has channels
with availability dates; `space` has a single speed.

The fix is *not* to invent a wormhole topology we don't have. It is the standing
rule: the checker cannot demonstrate this journey is impossible, because a
mechanism exists that it does not model. Once a transit channel is available, an
over-long journey is a **note**, not a hard finding, unless the endpoints can be
shown to be unconnected.

**1 × objects.** Alan, recorded dead, "acts in this chapter". The chapter says:

> *I hadn't thought of Carl, Karen, and Alan in, literally, centuries. … Alan
> **had been** a dedicated sailplane pilot.*

Past-tense reminiscence about a dead friend, read as present action.
`_acts_on_page()` has a careful docstring about not treating everyone named as an
actor; it doesn't handle a dead man being remembered. Same mention-vs-participation
family as `cast_json`, the 144 contradictions, and the epistemic knower list.

**With both fixed, step 1 is 0.**

## What step 1 was actually worth

The checkers went from 168 false alarms to 4 in one working session, and every
reduction came from the same insight rather than from tuning: *a check may only
raise `hard` when it has positively computed a contradiction.* Six instances of
that error have now been found. None of them was in the reasoning; all of them
were in what the code concluded from silence.

The number to quote once the better transcript exists is the one from a re-run,
not this. But the *shape* of the result won't change: the checkers are sound and
their severity discipline was not.

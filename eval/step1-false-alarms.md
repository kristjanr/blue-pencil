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

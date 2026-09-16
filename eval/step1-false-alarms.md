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

---

# Third run — 2026-09-16, with the wormhole channel

```
0 hard · 378 soft · 697 notes        target: ≤ 2 hard
71 of 71 chapters clean
```

**Step 1 passes.** 168 → 4 → **0**, in one working session.

The last three came from adding `space.channels: [{name: wormhole, available_from:
"book 5"}]` to the profile — deliberately with **no topology**. We do not know which
systems the WormNet joins or when it reached them, and a guessed map would be worse
than none: it would clear journeys that really are impossible. Naming the mechanism
only downgrades a finding to a note; it never clears anything, and before book 5 an
over-long journey is still a hard failure.

## What the run cost the checkers in credibility, and what it bought

Every one of the 168 was a checker fault, which is what step 1 is for. But they were
not 168 faults — they were **three**, and each was the same mistake: asserting a
contradiction the checker had not demonstrated.

That number should be re-taken against the better transcript when it exists. The
shape will not change; none of the three causes had anything to do with
transcription quality.

## Correction to a figure quoted throughout this project

The "391 citations whose quote appears nowhere" is **235**. 156 of the 391 verify
under a punctuation-insensitive comparison — the model had dropped quotation marks
around an interjection, or placed a curly quote at the other end of the line. One
was the *corpus* being wrong, not the quote: book 5.14.1 stores `Iwas in my VR lab`,
a space lost to drop-cap handling at ingest, which affects 11 book-5 scenes and
reaches everything downstream.

That correction has a consequence I had to undo. `bp ground --demote` capped 378
records at `inferred`/0.5 on the old number, and `demote_unproven` never recorded
what it overwrote. Restored from the pre-demotion snapshot
(`bobiverse-20260915-2025`, taken at commit `cbdadc6`): **142 records whose original
`claim_type` and `confidence` were put back**, being the ones that pass the
corrected check outright — 109 events, 16 promises, 8 threads, 7 entities, 2
objects. 234 remain correctly demoted. Several had been sitting at `inferred`/0.5
while their real value was `explicit`/1.0.

**Lesson worth keeping: a mutation that overwrites a value should record what it
overwrote.** The only reason this was recoverable is that `backup.sh` had run before
the demotion. That is luck standing in for design.

---

# Corpus repair: the drop-cap glue (2026-09-16)

**24 scenes in book 5** opened with the narrator's "I" glued to the next word —
`Iwas reviewing…`, `Istood at the edge…`. Repaired in place, recorded in
`record_changes` under run `dropcap-d1f5d5a5`.

## It is not an ingest bug

The EPUB itself is missing the space:

```html
<p class="class_s3j"><span class="class_s2y1">I</span>was reviewing the most…
```

Ingest concatenated faithfully. The drop cap sits in its own span and the
following text node begins `was`. Across book 5 the same markup appears as
`I|was` ×10, `W|e`, `T|he`, `I|t`, `B|ridget` — **identical markup, and gluing is
correct in every case except where the drop-cap letter is a word on its own.**
"T" + "he" is "The". "I" + "was" is not "Iwas". Only `I` and `A` can be damaged,
because they are the only single-letter English words.

## The first attempt was wrong, and the trail is why it cost nothing

I first tested only whether the *glued* token appears elsewhere in the corpus. It
does not — but neither does any proper noun book 5 introduces. That rule split
**Alexander → "A lexander" 87 times**, along with Atlantis, Alcubierre, Asimov and
Indiana, across 71 scenes.

`record_changes` had gone in that morning, at my own request, for exactly this
class of accident. Reverting was a `SELECT old_value` and an `UPDATE`. Verified
afterwards that `A lexander` was gone and that the original `Iwas` had come back —
restoring a defect being the proof that the restore was complete rather than
approximate.

Worth stating plainly: **the trail earned its keep within an hour of existing, on
its author's own mistake.**

## The rule that works — three conditions, all required

1. **Position.** Only a scene's opening word. Drop caps occur nowhere else, and
   this constraint alone saves `Ian McKellen` in book 5.37.1, which every lexical
   test flags and which is perfectly correct.
2. **Letter.** Only `I` or `A`.
3. **Lexicon, both ways.** The *tail* must be a word the series uses, and the
   *glued form* must be one it never uses. Testing only the second was the whole
   error: a new book's new names are, by definition, absent from the old books.

The corpus is its own dictionary, and the separation is total — `was` appears
5,077 times in books 1–4 and `iwas` zero. No external word list, which matters for
a series whose vocabulary is half invented.

One scene was missed by the sweep and fixed by hand: book 5.08.1's place line,
*"En route to Omicron2 Eridani"*, is long enough to look like prose, so the
opening-paragraph finder stopped on the header. A reminder that "skip the header"
is itself a heuristic.

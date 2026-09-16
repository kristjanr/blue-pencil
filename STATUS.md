# Where we are

Living status for the Bobiverse fan-continuation project. Background and the
engine's design live in [HANDOVER.md](HANDOVER.md) and [README.md](README.md);
this file is only *current state, who has what, and what happens next*.

Last updated: 2026-09-16 (second pass)

---

## The journey: six stages, two done

| | stage | what it means | state |
|---|---|---|---|
| 1 | **ingest** | EPUBs → scenes in a database | **done** — 836 scenes, books 1–5 |
| 2 | **extract** | scenes → the story graph: who, what, when, who knew it | **done** — all four passes |
| 3 | **plan** | thesis → skeleton → moves → card | barely started (one thesis run) |
| 4 | **draft** | write a chapter against its card | not started |
| 5 | **check** | the blue pencils — continuity marks | not started on real prose |
| 6 | **accept** | commit a chapter to canon | not started |

**Plan's four sub-stages**, since the names are opaque:
- `thesis` — how does the whole series end? Ranked by which open promises each
  ending pays off. This is the destination; everything downstream aims at it.
- `skeleton` — what shape is a book in this series? Fully deterministic: measures
  books 1–5 for chapter count, POV rotation, act breaks. Copies cadence, invents nothing.
- `moves` — what could happen next in a thread? A scored tournament; the top few
  keep their scores so a human can choose.
- `card` — the contract for one chapter: POV, date, place, cast, required reveals.

## Graph state

```
836 scenes · 16,393 citations
2,137 entities (from 2,407 — 270 merged: 110 mechanical, 155 Kris's review, 5 mine)
6,229 events · 881 objects · 2,886 promises · 1,967 threads · 2,414 style exemplars
0 open contradictions (all 144 were artifacts)
378 records demoted to `inferred` — their quote appears nowhere in the corpus
~$58 spent on extraction
```

Backups: `./backup.sh` → `gdrive:bookz/blue-pencil`, dated snapshots, integrity-checked.
**Run it before anything that mutates the graph.**

---

## The evaluation plan — a ladder, cheapest and sharpest first

The point of the backtest is **not** "did the engine guess book 6's plot". Even a
perfect engine would not: the author made free choices no promise ledger determines,
and a high plot-match score would suggest contamination rather than insight. The
real book 6 is a **control sample**, not an answer key.

1. **False-alarm rate.** Run the *real* book 6 through the checkers against a graph
   built from books 1–5. It is canon, so it cannot contradict them: every hard
   finding is a checker bug. No generation needed. Repo target: ≤ 2 false alarms.
2. **Recall.** Plant known errors in the real book 6 and see what is caught.
   Repo target: 18 of 20. (`tests/test_planted_errors.py` frames this as the
   project's gating exit test.)
3. **Shape.** `real_shape()` measures the real book 6 — chapter count, POV rotation,
   promise-payoff rate, new-entity rate. These become calibration rulers.
4. **Generate** the fake book 6 and score against those rulers.

**Steps 1–3 cannot be contaminated by knowing book 6.** Memory of the plot cannot
fake a working light-lag calculation.

**Blocked on, precisely** (checked 2026-09-16, and it is not what this file said):
a book 6 transcript *does* exist — `bookz/Dennis E Taylor - (Bobiverse 06) - The
Infinite Extent (transcript).md`, 77,525 words, cleaned, with known open items
listed in `bookz/work/transcription_notes.md`. It came from the **first** capture
(6h56m). A second, longer capture (8h54m, `recording_2026-09-13.flac`) exists to
re-check those open items but **has not been transcribed yet**. So the improved
text is not merely "in progress" — that pass hasn't started.
**Consequence:** step 1 can be *dry-run* against the existing transcript now, to
shake out mechanics, and re-run for a real number later. Don't quote a false-alarm
count taken from the first-capture text.

### How to drive step 4 when we get there
**Hands off on content.** Gates to auto; the engine takes its own top thesis and
its own top move every time. Kris makes no plot decisions — that is the blinding.
His knowledge of book 6 is for *scoring*, never for *steering*.

Before starting: write predictions down first, and don't re-read book 6 beforehand.

### Why this matters now
The model is uncontaminated on book 6 — probed: knows the series through book 4,
recalled zero events from a sixth. That property **expires** when future models
train on book 6. This is a clean benchmark we only get once.

---

## Forwarded to the `blue-pencil-3c` session (software work)

Kris's split: that session improves the software; this session is the *user* of it.

**Bugs already fixed here and committed** (told them for context, no action needed):
batch `custom_id` sanitising; the `_salvage` layer (per-record repair instead of
losing a scene to one bad field); demotion of 391 unprovable records; contradictions
requiring both readings cited; `prune` never merging entities at all.

**Open items handed over:**

`blue-pencil-3c` reported items 1, 3, 4 and 5 fixed on 2026-09-16 (commits
`27ff3d8`, `092d110`, `8a1371c`) and moved on to the three design features.
Verified here against the real graph: 152 tests pass, the units bug is genuinely
gone, the `entity_merges` table migrates. **But two larger defects were sitting
underneath item 1** — see "What the fix uncovered" below.

| # | item | state |
|---|---|---|
| 1 | `evidence_score` units bug — a **count** divided by a **summed weight** | **fixed** — but the term still has no range; see A below |
| 2 | Planner sees 120 of 2,842 promises, 60 of 1,865 threads | **open** — now the binding constraint; see A |
| 3 | `-n` not honoured — asked for 2 hypotheses, got 1 | **fixed** — asks once more for the shortfall |
| 4 | Entity merges drop the absorbed **id** | **fixed** — new `entity_merges` table. Retroactively empty here: this graph's 270 merges predate it, so those ids stay unrecoverable |
| 5 | `structured()` fails on `resolve.py`'s `Verdict` schema | **fixed** — `tool_choice` was `auto`, so the model could answer in prose |
| 6 | Term-grounding can't tell a name from a descriptor | open — don't gate on it; the quote half is exact and fine |
| 7 | `bp ground` takes a write lock on open | open — can't run while an extract holds the DB |
| 8 | Fabricated-**entity** detection ("Alexander", "Charlie") | **tried, reverted** — see below |

### Item 8 — tried a name-existence check, it doesn't work

Built `check_entity_existence`: flag an entity whose name (and every alias)
appears in none of its cited scenes. Tested against the real graph before
shipping it: **453 flagged, almost all false positives** — the extractor
legitimately labels unnamed minor characters with descriptions ("Panel
Moderator", "Rosie's mother", "Wagon Driver") that were never meant to be
verbatim quotes. Restricting to `kind='character'` and filtering
"unnamed"/possessive markers got it to 35, still dominated by the same
problem (`"Bridget Brodeur"` is only ever called `"Bridget"` in the text —
a real character, a false positive from checking the full name).

Worse: it does not catch the motivating case. `"Alexander"` genuinely
appears in the corpus — three different legitimate characters are named
that. The actual defect is that the extractor *also* invented a fourth,
a Bob-copy/replicant Alexander, conflating him with the others. That is a
semantic identity judgment (same class as `"Charlie"`'s form contradiction),
which is exactly what `resolve.py`'s `Verdict.defects` field is for.
`resolve.py`'s `candidates()` already groups all three real "Alexander"
records plus the invented one into one candidate — the adjudicator, once
run, is the fabricated-entity detector. Reverted the check rather than ship
something that would demote hundreds of legitimate records' confidence.

### What the fix uncovered — measured on the real graph, 2026-09-16

**A. The evidence term still can't discriminate.** The prompt shows the top 120
open promises by weight: 4.2% of the 2,842 open promises, carrying 6.8% of their
total weight (102.7 of 1514.3). Coverage is divided by the weight of *all* open
promises, so a thesis that paid off **every promise it was shown** scores coverage
0.068 → an evidence term of ~0.041, against a self-report term reaching 0.40.
**The ranking is ~90% self-report even at its ceiling.** The saved thesis measures
0.0119 evidence vs 0.2000 self-report. Suggested fix: normalise coverage against
the weight actually *offered* to the model, not the whole ledger.

**B. Nothing ever closes a promise.** 2,842 open / 41 paid / 3 abandoned, of 2,886.
Threads: 1,865 open / 51 dormant / 51 closed, of 1,967. Across five *finished*
books, 98.6% of promises are recorded as never paid off — there is no pass that
settles a ledger entry when later text cashes it. This is why A bites: the
denominator is inflated by roughly the whole corpus, and the planner's window is
filled with setups book 3 already resolved. **Book 6 is being planned around dead
material.** Needs a "settle the ledger" pass over books 1–5 before any thesis
ranking is trusted. This is the biggest open item in the project.

**C. `planted_in` was never pinned the way citations were.** 46 promises point at
a scene id that does not exist (`book5.15.3`, `B2.66.1` — the same reformatting
that produced 245 dangling citations), plus 4 with an empty `planted_in`.
`_write_records` pins `citations` to the scene the request carried but not
`planted_in`. Repair is mine to run once the pin lands.

**Design requests forwarded** (the human currently has no way to *speak*, only to veto) —
**all three done 2026-09-16** (commits `da5ed2e`, `f6955a3`):
1. ~~`ChapterCard` should carry free-text **intent**~~ — `card.feel`, surfaced in the scene brief the drafter reads, kept out of what the card checker verifies.
2. ~~`bp check --serve` needs a **text box**~~ — `serve_review` now returns `(verdict, note)`; `revise()` takes `human_note` as an instruction to follow; `cmd_check` calls `revise()` itself on a "revise" verdict and writes `*.revised.md`.
3. ~~`accept` should record **why**~~ — `bp accept --note "..."` persists to a new `editor_notes` table; `Graph.editor_notes()` reads it back.

**Framing sent with them:** the software's output is not the book. It is a graph
that knows what is true, a plan that aims at a chosen ending, and a checker that
catches a contradiction with book 3 in chapter 40. The prose comes from Kris and
the agent working together; a draft is a first pass that survives checking.

**WIP handed over:** `bp/resolve.py` + `eval/entity_resolution_labels.yaml`
(Kris's verdicts on 126 groups — the gate for any adjudicator).

---

## Entity duplication, round two — found 2026-09-16

The cast rebuild (`1ff7050`) fixed scene cast, and in doing so exposed that
**44.1% of cast entries name a character that exists as more than one record**
(1,642 of 3,722). The top of the cast reads `Bob-1` ×323 *and* `Bob Johansson`
×308; `Riker` ×175 *and* `Will (Riker)` ×116.

This is worse than the junk it replaced. `knowledge.py:last_placement()` matches
cast against one canonical name, so a split character is invisible in half his
scenes and the geography checker returns a stale position **with full confidence**.
Junk cast was noise; a split character is a false alarm with a straight face — and
step 1 of the ladder is a false-alarm count. **This gates step 1.**

Verdicts on all 32 groups: `eval/entity_duplicate_verdicts.yaml` (`a798b06`).
23 merge, 2 partition, 3 stay split, 6 held for Kris. 38 records absorbed.
Waiting on `blue-pencil-3c` for `merge_group()` + a `bp resolve` CLI to apply it —
`resolve.py` has `adjudicate()` but no command, so the adjudicator can't run either.

**Three findings worth keeping:**
- **Frieda is stated distinct in the text** — Bob explicitly confirms this Frieda
  is not the one he knew. The only place the series says distinctness out loud.
  Any adjudicator that merges Frieda is broken in the expensive direction.
- **Spike and Guppy aren't an identity question.** Each Bob runs his own instance
  of the same VR cat / GUPPI. The graph can't say *same type, different instance*,
  so this keeps arriving as a merge/split question the schema can't answer.
- **Four of the six holds are the Charlie shape**: two records contradicting on an
  invariant (Kiroshi is both "a human general" and "a Bobiverse replicant"; same
  for Richards). Not a merge question — one description is simply false. With
  Alexander's "Bob-copy" that is **three instances of a true citation carrying a
  false description**, and it probably deserves its own checker.

### Waiting on Kris — six groups
`Bender` vs `Bender's Matrix` (person or substrate?) · `Kiroshi` and `Richards`
(contradictory records) · `Christie Campbell` vs `Ser Campbell` (one leader in two
places, or two colonists?) · the bare `Steven` (inside Heaven's River, where a
human professor shouldn't be) · and the modelling question behind Enoki: **should
an undercover persona be its own entity?** Bob-1 is not the only Bob who goes
under a name, so that answer sets a rule for the whole graph.

## Entity groups deliberately left unmerged

`Charlie` (records contradict), `Belinda` (different,
low confidence), `Guppy` (every replicant has one — same kind, maybe distinct
instances), `Harvey` (different), `Survey Drone` (several genuinely exist),
`Kevin`'s CryoEterna rep (the other two merged).

**Correction, 2026-09-16: `Alexander` is not fabricated.** Verified against the
corpus — the name appears in 26 scenes of book 5, and all four `character` records
cite quotes that check out. They are one dragon warlord recorded four ways
(`alexander_dragon`, `dragon_conqueror`, `alexander`, `alexander_bobiverse`) and
should probably all merge. The thing Kris flagged was real but narrower: the
*description* on `alexander_bobiverse` calls him "a Bob-copy", which is a category
error — Alexander is a dragon; the Bobs in that storyline are Bridget and Howard.
**A third failure class:** a true citation carrying a false description. Neither
the fabricated-quote check nor `bp ground` can see it, because every term does
appear in the cited scene. **Awaiting Kris's call on the four-way merge**, since it
overrides a verdict he gave while reading that same wrong description.

## Decisions already made — don't re-litigate

- Silence on a reviewed entity group means **same entity**.
- Merging two people is worse than leaving one split: **merge only on positive
  evidence**, default to split. More context helps merging and hurts splitting,
  because identity can be stated in the text and distinctness never is.
- Enums are never guessed during salvage — a belief that can't be read is dropped,
  not assigned a polarity.
- Unprovable records are **demoted, not deleted** — they are probably true.
- Never run `secret-tool search` (it prints the key); use `secret-tool lookup … | wc -c`.

## Next steps, in order

1. Wait on the improved book 6 transcription.
2. **Step 1 of the ladder** — real book 6 through the checkers, count false alarms.
3. **Step 2** — planted errors, measure recall.
4. **Step 3** — `real_shape()` calibration rulers.
5. Do **not** trust a thesis ranking until finding **B** (the unsettled ledger)
   and finding **A** (coverage normalised to what the model was shown) are both
   fixed. The `evidence_score` arithmetic fix alone is not enough. The thesis sets
   the destination every later chapter aims at.
6. **Step 4** — generate fake book 6 hands-off, score against the rulers.
7. Then book 7, with Kris fully in the loop.

## Loose ends

- Four superseded audio files still on disk awaiting `rm` (the sandbox blocked me):
  `loopback_test3.wav`, `loopback_test2.wav`, `work/audio16k.wav`, `loopback_trimmed.flac`.
  Two others (`loopback_full.flac`, `loopback_7h.flac`) are **not** on Drive — upload
  before deleting if they matter.
- Display docs: the spot audit (`caYgQqNC`) and the entity review (`qCmk8is4`),
  both private, both fully acted on.

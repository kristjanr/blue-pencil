# Where we are

Living status for the Bobiverse fan-continuation project. Background and the
engine's design live in [HANDOVER.md](HANDOVER.md) and [README.md](README.md);
this file is only *current state, who has what, and what happens next*.

Last updated: 2026-09-15

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

**Blocked on:** the improved book 6 transcription (in progress elsewhere — the 1.0×
recording). Steps 1–2 need that text.

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

| # | item | why it matters |
|---|---|---|
| 1 | `evidence_score` units bug — `paid / total_weight` divides a **count** by a **summed weight** | Ranking is **96% the model's self-assessment**. Measured: evidence term 0.0081 vs self-score 0.2000. Defeats the project's central claim. |
| 2 | Planner sees 120 of 2,842 promises (hardcoded `[:120]`), 60 of 1,865 threads | A thesis is proposed against 4% of the ledger. Compounds with #1 and hides it. |
| 3 | `-n` not honoured — asked for 2 hypotheses, got 1 | |
| 4 | Entity merges drop the absorbed **id** (keep only the name) | After merging, old ids no longer resolve. |
| 5 | `structured()` fails on `bp/resolve.py`'s `Verdict` schema | **Narrow, not a shared-path defect** — `bp plan` goes through the same path fine. Earlier alarm corrected. |
| 6 | Term-grounding half of `bp ground` can't tell a name from a descriptor | Flags `unnamed`/`unknown`/`investigation`. Don't gate on it. Quote half is exact and fine. |
| 7 | `bp ground` takes a write lock on open (stamps schema_version) | Can't run while an extract holds the DB. |
| 8 | Fabricated-**entity** detection | Kris found "Alexander" (doesn't exist) and "Charlie" (two records contradicting on an invariant fact). Same class as the 391 fabricated quotes. |

**Design requests forwarded** (the human currently has no way to *speak*, only to veto):
1. `ChapterCard` should carry free-text **intent** ("what this chapter should feel like"), fed to the drafter.
2. `bp check --serve` needs a **text box**, and the rejection reason must reach the reviser as first-class input. *Highest value — closes an open loop.*
3. `accept` should record **why**, so the graph accumulates the editor's taste.

**Framing sent with them:** the software's output is not the book. It is a graph
that knows what is true, a plan that aims at a chosen ending, and a checker that
catches a contradiction with book 3 in chapter 40. The prose comes from Kris and
the agent working together; a draft is a first pass that survives checking.

**WIP handed over:** `bp/resolve.py` + `eval/entity_resolution_labels.yaml`
(Kris's verdicts on 126 groups — the gate for any adjudicator).

---

## Entity groups deliberately left unmerged

`Alexander` (fabricated), `Charlie` (records contradict), `Belinda` (different,
low confidence), `Guppy` (every replicant has one — same kind, maybe distinct
instances), `Harvey` (different), `Survey Drone` (several genuinely exist),
`Kevin`'s CryoEterna rep (the other two merged).

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
5. Wait for `evidence_score` fix before trusting any thesis ranking — the thesis
   sets the destination every later chapter aims at.
6. **Step 4** — generate fake book 6 hands-off, score against the rulers.
7. Then book 7, with Kris fully in the loop.

## Loose ends

- Four superseded audio files still on disk awaiting `rm` (the sandbox blocked me):
  `loopback_test3.wav`, `loopback_test2.wav`, `work/audio16k.wav`, `loopback_trimmed.flac`.
  Two others (`loopback_full.flac`, `loopback_7h.flac`) are **not** on Drive — upload
  before deleting if they matter.
- Display docs: the spot audit (`caYgQqNC`) and the entity review (`qCmk8is4`),
  both private, both fully acted on.

# Blue Pencil

An engine that takes an unfinished long series and produces a continuation the
author's own readers would argue about — built as an editorial pipeline, not a
prompt.

This is an implementation of the [engineering plan](docs/PLAN.md) (v0.3).

---

## Why it is shaped like this

Four commitments from the plan drive every design decision in the code, so it is
worth stating them before the file listing.

**The context window is working memory; the story is the database.** A five-book
epic can run to 2.4M tokens. Even a series that *fits* in a 1M context should not
be dumped there: "having read everything" is not the same as "knowing what this
character currently believes, and when they could have learned it". So the books
are turned into a cited, queryable graph once, and every generation step
retrieves a small exact context pack from it.

**Separate what happens, how it is written, and whether it is consistent.**
Three jobs, three failure modes, never one prompt.

**Most of the value is in the marking, not the typing.** A blue pencil is what an
editor marks a manuscript with. The continuity, voice and repetition checkers are
the part no previous attempt at this bothered to build, so they ship first and
they work on *anyone's* chapter.

**The human is editor-in-chief.** The engine produces candidates and marginalia.
Taste, boredom and "no" stay human.

### The decision that shapes the code most

**Anything that can be arithmetic is arithmetic.** The engine never asks a model
whether a character could know something yet — it does the division.

> Vela is twelve light years from Sol. Cyra sees the relay destroyed on
> 2180-06-01 and transmits by radio. Ana learns of it on **2192-06-01**.

Nothing in the graph states that arrival date. It is computed from the profile's
distance table and the channel's speed, at write time and again at query time,
by the same code. That one choice has three consequences that run through
everything else:

1. **The checkers are exact.** A continuity finding is a shortest-path result
   with a date attached, not an opinion that might differ next run.
2. **Most of the engine runs with no API key.** Ingestion, all four hard
   checkers, the repetition auditor, the voice metrics, the book-shape
   measurement and Tier B of the backtest are deterministic. That is what makes
   it possible to develop the editor in a fast loop, and to test it.
3. **The model spend goes where judgment actually lives** — extraction,
   invention, prose, and the panel.

---

## What it does

```
corpus → scenes → story graph → plan → draft → check → accept → (the world advances)
                        ↑                                  │
                        └──────────────────────────────────┘
```

| Stage | Module | Needs a model? |
|---|---|---|
| 1 · Ingest — EPUB/text to scenes, FTS + vector index, per-POV baselines | `bp/ingest.py` | no |
| 2 · Extract — events, reports, beliefs, consequences, promises, threads, technique specs | `bp/extract.py` | yes (batch) |
| 3 · Plan — ending hypotheses, book skeleton, move tournament, chapter cards | `bp/planner.py` | partly |
| 4 · Draft — context pack assembly, N candidates, revision | `bp/draft.py` | yes |
| 5 · Check — nine blue pencils | `bp/checks/` | four of nine |
| 6 · Accept — re-extract, write deltas, git commit | `bp/accept.py` | recommended |
| Eval — contamination probe, Tiers A/B/C, ablations | `bp/evalharness.py` | probe only |

### The core query

`bp/knowledge.py` answers **"is there an information path from this event to
this character by this date?"** — a shortest-path problem where a relay cannot
forward what it does not yet know, so the relaxation is over earliest *arrival*
time. Three things put knowledge in a character's hands: observation, a report
over a channel, and — for series where minds are copied — inheritance at the
moment of a fork. Everything else, including "obviously they'd have heard by
now", is not a path, and the checker says so instead of assuming.

```
$ bp graph knows --event E-001 --character Ana
Ana · event E-001
  earliest knowledge: 2192-06-01
  path: Cyra present at the event (2180-06-01) · Cyra → Ana via radio computed, arrives 2192-06-01
```

### The nine checks

| Check | Kind | What it asks |
|---|---|---|
| `epistemic` | hard, deterministic | Could everyone on the page know what they act on? |
| `geography` | hard, deterministic | Could these bodies be in this room on this date? |
| `objects` | hard, deterministic | Dead stays dead; a sword is not in two places. |
| `card` | hard, deterministic | Are the carded reveals here, and only those? |
| `repetition` | soft, deterministic | Is a phrase used harder than canon ever used it? |
| `voice` | soft, deterministic | How far is this from the POV's measured technique spec? |
| `consequence` | soft, deterministic | Does anything here change a future state? |
| `discriminator` | floor, model | Can a blind judge pick this out as generated? |
| `panel` | soft, model | Three readers who want different things. |

Severity comes from the run policy, not the checker. Findings are never collapsed
into one number — a single score hides exactly the trade-offs an editor needs to
see.

---

## Verified behaviour

The Phase 2 exit test from the plan, run against a synthetic series
(`tests/fixtures/synthetic.py`) whose every distance and date is known exactly:

```
20 planted errors  →  20 caught          (target: ≥18)
clean chapter      →   0 hard findings   (target: ≤2 false alarms)
```

The planted errors are real violations with known correct answers: seven
references nobody on the page could have heard yet, two characters who could not
have travelled that far, a dead man acting, an object six light years from where
the registry left it, three broken card contracts, two repetition budget
overruns, and three voice-drift measurements. `tests/test_planted_errors.py`
names each one and the checker that must catch it; a finding of the right kind in
the wrong place does not count.

`94 passed` — `python -m pytest`. The suite runs offline in about five seconds.

**What is proven vs. what is wired.** Every deterministic stage above is
implemented and tested end to end. The model-backed stages — extraction,
planning, drafting, the discriminator, the reader panel, the contamination probe
— are implemented against the real API (structured outputs, batch, prompt
caching, streaming, refusal handling) but have not been run against it here,
because this build has no API key. Their prompts and schemas are real code, not
placeholders; they have not been calibrated on a live corpus.

---

## Quick start

```bash
pip install -e .

bp init                                   # scaffold a workspace
# put EPUBs (or .txt/.md) in corpus/, then edit a profile

bp profile --profile bobiverse            # confirm what the profile asserts
bp ingest --profile bobiverse             # deterministic; no API key needed
bp extract --profile bobiverse --batch    # the expensive one-time step
bp audit --profile bobiverse -n 50        # the Phase 1 spot-audit worksheet

bp check chapter.md --profile bobiverse --html review.html
bp check chapter.md --profile bobiverse --serve       # a = accept, r = reject

bp probe --profile bobiverse --book "book 6"          # contamination control
bp backtest --profile bobiverse --hide book5

bp plan thesis --profile bobiverse --run book6-draft
bp plan moves  --profile bobiverse --thread T-004 --depth 2
bp plan card   --profile bobiverse --book book6 --chapter 17 --pov Bill
bp draft book6.ch17 --profile bobiverse --run book6-draft
bp accept drafts/book6.ch17/chapter.md --profile bobiverse --llm

bp state Riker 2189-04-01 --profile bobiverse         # a checkout, not a guess
bp cost --corpus-tokens 700000 --book-words 110000
```

`bp check` exits `0` when every hard check passes and `2` when it does not, so it
drops straight into a loop or a CI job.

---

## The two config files

Nothing in `bp/` knows about any series. Everything series-specific is a profile;
everything run-specific is a policy. Both ship with working examples.

**`profiles/*.yaml` — what is true about the series.** The
`information.channels` block is the reason the file exists: it turns "could they
know this yet?" into a computation. For a light-lag space opera that is radio
versus an instant relay; for a court intrigue it is ravens, riders and roads; for
a small-town novel it is gossip, and gossip is instant.

Two profiles ship, deliberately from different genres, to demonstrate that the
same checkers run on both:

```
profiles/bobiverse.yaml       interstellar · light years · clone lineage · revivable
profiles/court-intrigue.yaml  travel_table · road-days   · regnal calendar · permanent death
```

**`runs/*.yaml` — how this run behaves.** Gates, candidates per scene, model per
stage, context budgets, check severities, spend cap. Everything an earlier draft
of the plan called "a decision to make before building" is a setting here,
changeable per run and per act. `runs/editor-only.yaml` is the Phase 2 product:
no generation, every checker at its strictest.

---

## Repository shape

```
bp/                  the engine — knows no series
  timeline.py        in-world dates on one axis, with uncertainty preserved
  space.py           distances; the two space models
  profile.py         the series profile
  policy.py          the run policy
  models.py          record schemas, shared by structured outputs and the DB
  db.py              SQLite + FTS5; citations enforced at write time
  knowledge.py       ← the core: temporal reachability over the report graph
  ingest.py          stage 1
  extract.py         stage 2 (+ the prune pass that keeps contradictions open)
  planner.py         stage 3
  draft.py           stage 4 (context pack assembly)
  checks/            stage 5 — nine blue pencils
  accept.py          stage 6
  evalharness.py     probe, tiers, ablations
  review.py          the review page
  llm.py             the one place the engine talks to a model
  cli.py             bp
profiles/ runs/ graph/ corpus/ plan/ drafts/ accepted/ eval/
tests/               94 tests, all offline
```

Boring on purpose. SQLite rather than a service, because a single file is
trivially branchable with git: an alternate continuation is a branch, and a wrong
turn in chapter thirty is a reset rather than a rewrite. No web app until the CLI
proves the loop.

---

## Two invariants worth knowing before you edit anything

**A claim with no citation is not written.** `bp/db.py` raises `UncitedClaim` at
write time. But a citation is *necessary, not sufficient* — it proves the text
contains evidence related to the claim, not that the claim is right. So every
record also carries a claim type (`explicit` / `inferred` / `disputed`), a
confidence, and any alternative readings.

**Contradictions are stored, never resolved.** When two scenes will not
reconcile, the prune pass opens a contradiction record holding both readings. In
a series built on unreliable narrators, the contradiction is often the story, and
a pipeline that silently picks a winner has thrown away the evidence. The bible
digest handed to the drafter lists them under *"do not settle these by
accident"*.

---

## Known limitations

- **Mention detection is the weakest link in the hard checks.** Everything
  downstream of "this chapter refers to event E-0311" is exact; deciding *that*
  is a language problem. The offline path is lexical with a locality and
  anchoring test, tuned so a clean chapter produces no false alarms. `--llm` adds
  a cheap precision pass. Paraphrase without shared vocabulary is still missed.
- **Embeddings are local.** `bp/embed.py` is a deterministic hashed tf-idf, not a
  hosted embedding model — reproducible and offline, but weaker at paraphrase.
  `set_embedder()` swaps it; re-run `bp ingest` afterwards.
- **Turning-point detection is a word-count proxy.** Good enough for Tier A shape
  comparison, not a theory of narrative structure.
- **The model-backed stages are uncalibrated.** See "proven vs. wired" above.
- **The plan's cost figures are estimates.** `bp cost` reproduces them from unit
  rates; it is an order of magnitude, not a quote.

---

## On the corpora

This is fan work for private reading. Several of the authors whose series make
good corpora are on record against AI use of their books. The engine builds a
technique *specification* from published text and retrieves exemplars at runtime;
**it does not fine-tune on the books**, and nothing it produces is for
distribution. `corpus/` is gitignored. Keep it that way.

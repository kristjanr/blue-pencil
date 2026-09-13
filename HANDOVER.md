# Handover — 2026-09-13

State of play after a working session on Blue Pencil, written so the next
session does not have to re-derive any of it.

## The goal, in one paragraph

Kris is writing a fan-fiction Bobiverse book 7 with this engine. Before that,
the plan is a **backtest**: feed the engine books 1–5 only, have it write its own
book 6, and score that against the real book 6 — *The Infinite Extent*, an
Audible Original he recorded and transcribed himself (see
`~/Projects/bookz`, and its own `HANDOFF-macOS.md`). The author's tentative
title for the real book 7 is *The End of All Things*, and he has said book 7
closes the main timeline.

**Two goals that want opposite things, and must not be blended.** Validating the
engine needs a hands-off run — every human judgement is contamination. Getting
an enjoyable book wants maximum taste. Kris chose: **strict engine-only for the
backtest**, human involvement afterwards for book 7.

## The contamination question is settled, and the answer is good

`bp probe` was run against the live API. Opus, asked cold, said it knows the
series through book 4, is aware of book 5, and has **no knowledge of a sixth
volume** — zero events recalled, and it declined to invent. Saved at
`eval/probe/bobiverse-The Infinite Extent (Bobiverse book 6).json`.

**Book 6 is uncontaminated; the backtest is valid.** That is the premise the
whole plan rests on, and it now has evidence.

One caveat that limits what the backtest can measure: the book 6 transcript has
**no quotation marks** — Whisper does not emit them — so dialogue ratio reads as
zero and any voice metric run against it measures the transcription, not Taylor.
Repair that before using book 6 for voice comparison.

## What was wrong, and is now fixed

Seven commits, all pushed to `master`. Every one was found by running the thing
against real data and noticing numbers that disagreed with each other.

| Commit | What it fixes |
|---|---|
| `9c843c8` | EPUB ingest: text counted twice, drop caps splitting words, chapter heads not found, `book_id` breaking the SCUT lookup |
| `bfb4f55` | The epistemic check now refuses to run when a channel's availability is unresolved, instead of silently passing everything |
| `cbe6726` | The model-backed calls brought to the current API — `thinking`, `temperature`, forced `tool_choice` |
| `dd28306` | Book 3's POVs recovered; a place catalogue replacing the six-entry pair file |
| `d11b85c` `69fa0b1` | The contamination probe was inverting its own verdict, and its test agreed with the bug |
| `f547fe5` | Scene breaks printed as ornaments rather than typed — 212 of them, invisible |

Measured effect on the corpus:

```
words        964,202 -> 523,382   (duplication removed)
scenes           661 -> 836       (ornament breaks found)
POV assigned      0% -> 96%
dated             0% -> 96%
placed            0% -> 61%
distinct POVs     63 -> 26        (44 were chapter titles)
SCUT relay   unresolved -> live from book 2
```

The deterministic reasoning core — `knowledge.py`, `space.py`, `timeline.py` —
had **no bugs**. Every fault was in reading messy input or in the model-backed
calls. Worth remembering when the "should this just be an LLM?" question comes
back: the answer that fits the evidence is an LLM at *ingest*, determinism in
the *reasoning*.

## Where extraction stands — read this before spending

`bp extract` **works**. A 3-scene smoke test produced 27 entities, all cited,
none rejected, and the records are good (it marked a character only mentioned in
passing as `unknown` rather than asserting). Prompt caching is working: 81%
of input tokens were cache reads.

Three schema-level rejections were found and fixed **at $0.00**, because a 400
fires before any generation. Smoke-test before the real spend; it paid for
itself three times over.

**But two numbers are not what the plan assumed:**

- **Cost.** Measured **$0.040/scene** for one pass. Naively that is 836 scenes ×
  4 passes ≈ **$134**, or **~$67 with `--batch`** — against the $10 `bp cost`
  estimated. The measurement that would settle this was attempted twice and
  killed both times (see below), so the real figure is still unknown. Best
  guess **$30–70 batched**, because the sampled scenes were chapter-one
  cast-introduction scenes, which are the most entity-dense in the corpus.
- **Speed.** Synchronous extraction runs at roughly **40 s/scene**. The full
  corpus would be ~37 hours that way. **`--batch` is not the cheap option, it is
  the only practical one** — it parallelises as well as halving the price.

### Unfinished: the steady-state cost measurement

Two attempts to measure the rate on mid-corpus scenes both died:

1. `timeout 1500` on a job estimated at 27–40 min — killed at 25 min, and since
   `extract()` commits at the end of a pass, **~35 scenes of paid work was
   discarded**. About $1.40 wasted.
2. Rewritten to commit per scene, then killed by the harness's background-task
   reaper. Output was piped through `tail`, which buffers, so no partial
   progress was visible either.

**If you retry it:** do not pipe through `tail` (it hides progress and makes a
killed run unreadable), keep the per-scene commit loop, and give it a timeout
well above the estimate. `/tmp/midsample.py` holds the last version. Or skip the
measurement entirely and run `--batch` over the whole corpus, accepting ~$67.

## Uncommitted work

```
M bp/llm.py      the strict-schema fallback: `strict: true` is tried, and
                 dropped for this run if the API rejects the schema as too
                 complex. This is what made extraction work. TESTED, 94 green,
                 but NOT YET COMMITTED.
?? eval/         the saved probe result. Worth keeping.
```

Commit `bp/llm.py` before anything else — extraction does not work without it.

## Environment

- **API key** is in the login keyring: `secret-tool lookup service anthropic key api`.
  `./bp-run <any bp command>` fetches it at launch. **Never run
  `secret-tool search`** — it prints secrets; that is how a key got exposed in
  this session and had to be rotated. `lookup | wc -c` is the safe check.
- Venv at `.venv`, installed `-e`. 94 tests, offline, ~2 s.
- Corpus in `corpus/` is books 1–5 only. **Book 6 must stay out of it.**
- `graph/bobiverse.sqlite`: 5 books, 836 scenes, 26 style baselines, 49,581
  phrases, 24 entities (from the smoke test), 0 events.

## Known gaps, in the order I would take them

1. **Commit `bp/llm.py`.**
2. Settle the extraction cost, or accept ~$67 and run `--batch`.
3. 20 scenes still have no POV, 319 unplaced. Both are parsing/data tails.
4. Places marked `series?` in `profiles/data/bobiverse-places.csv` are my
   inferences, not catalogue data — Poseidon, Quin, and the Heaven's River
   interior locations. A wrong distance makes the checker confidently wrong.
5. `travel_speed: 0.5c` in the profile predates the later books' faster drives.
   A geography finding on a book 4–5 chapter may be a calibration artefact
   rather than a real catch.
6. Repetition fires 3 hard findings on Taylor's own prose, against the plan's
   "≤2 false alarms" bar. It compares a chapter to a whole-POV mean, so quiet
   chapters always look wrong.

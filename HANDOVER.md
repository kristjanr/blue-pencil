# Handover — 2026-09-13

Everything a fresh session needs. Written because the conversation that produced
all of this is about to be compacted, and none of it should have to be
re-derived.

---

## 1. The end goal

**Kris wants to read Bobiverse book 7 before Dennis E. Taylor writes it**, and
would rather build the machine than wait. So the real project is the machine:
Blue Pencil, an engine that takes an unfinished long series and produces a
continuation. The book is its output, not the point.

Two things about the real book 7, from the author's own status page
(dennisetaylor.org/status-of-things, dated 2026-07-12):

- His tentative title is ***The End of All Things***. ("No, everyone doesn't
  die.")
- **Book 7 is planned to close the main timeline.** The series has been written
  as a linear future history; later Bobiverse books will exist but be
  non-linear stand-alones. So a fan book 7 has to *land an ending*, not set up
  a book 8. That is a real constraint on the plan.

### Before book 7: the backtest

Feed the engine **books 1–5 only**, have it write its own book 6, and score that
against the real book 6 — ***The Infinite Extent***, an Audible Original that
Kris recorded off his own playback and transcribed himself.

That book is the reason any of this is measurable. It is an Audible Original
from 2026 that **no model has in training data**, verified (see §4). A held-out
text that the thing being tested has never seen is scarce, and he manufactured
one.

### The decision already taken about rigour

The two goals want opposite things:

| | wants |
|---|---|
| Validate the engine | a hands-off run — every human judgement is contamination |
| Get a book worth reading | maximum taste and iteration |

**Kris chose: strict engine-only for the backtest, then full involvement for
book 7.** Don't blend them. And note the corollary — *the assistant is also
contamination*. Whoever has read the real book 6 must not write, steer, or score
the fan book 6. `bp draft` sees only the context pack assembled from a graph
built on books 1–5; the engine is the firewall.

---

## 2. Where everything lives

| What | Where |
|---|---|
| The engine | `~/Projects/blue-pencil` — git, pushed to `github.com/kristjanr/blue-pencil` |
| The transcription project | `~/Projects/bookz` — **not** a git repo |
| Mirror of bookz | `gdrive:bookz/` via rclone (`~/.local/bin/rclone`), 179 objects |
| API key | login keyring — `secret-tool lookup service anthropic key api` |
| Memory | `~/.claude/projects/-home-kris-Work/memory/` (4 entries + index) |
| Transcription notes | `~/Projects/bookz/work/transcription_notes.md` |
| macOS-side handoff | `~/Projects/bookz/HANDOFF-macOS.md` |

The machine is an **Apple M2 running Linux** (Omarchy/Arch ARM, bare metal —
`systemd-detect-virt` says `none`). It **dual-boots into macOS**, which matters
for two planned jobs, both in §5.

---

## 3. Thread A — the transcription (`~/Projects/bookz`)

### Done

*The Infinite Extent* exists as an epub:
`Dennis E Taylor - (Bobiverse 06) - The Infinite Extent (transcript).epub`

- 71 chapters, ~77.5k words, from a 6h56m PipeWire digital-loopback recording of
  the Audible narration, transcribed with faster-whisper `large-v3-turbo` on CPU
  (2.83× realtime, 2h27m).
- Typeset to match the retail books — checked against Kris's own screenshots of
  books 4 and 5 in Apple Books: small-caps chapter headings, italic
  narrator/date/place byline, drop caps, justified indented prose, `* * *` scene
  breaks, plus front matter (Titles by, title page, dedication, epigraph, and a
  note stating provenance).
- Cover: the Audible artwork, extended to 2:3 portrait by an image generator and
  re-set in the series' chrome lettering (Michroma + gradient + outline).
  `work/make_cover.sh` rebuilds it.
- Source of truth is `work/chapters_formatted/*.md`; the epub is **generated** by
  `work/build_epub.py`. Never hand-edit the epub.
- **`work/format_book.py` overwrites the hand-fixed chapter files.** Only re-run
  it after a fresh transcription.

### Known quality limits

`work/transcription_notes.md` has the full list. The two that matter:

- **No quotation marks anywhere** — Whisper does not emit them. So dialogue
  ratio reads as zero, and any voice metric run against this text measures the
  transcription, not Taylor. Repair before using book 6 for voice comparison.
- Chapter 70's title is truncated ("Bob's Discuss"), and one passage at ~5h00m
  stayed garbled through two transcription passes.

One correction worth keeping: the Bobiverse location is **"In Virt"** (the
counterpart to "in real"). Whisper hears "invert" every time.

### Live right now — a second recording is in progress

```
parecord --device=monitor_mic.monitor --rate=48000 --channels=2 --file-format=flac
         /home/kris/Projects/bookz/recording_2026-09-13.flac
```

Started by a **second agent session** on the same machine (Kris was connected to
two). At last check: **6h26m captured, 728 MB, still running**, wrapped in
`systemd-inhibit --what=idle:sleep:handle-lid-switch` so sleep cannot kill it.

This is the **1.0x-speed** take. The first recording was played at 1.3×, and the
hypothesis is that normal-speed audio transcribes more accurately. When it
stops:

1. It is excluded from the gdrive sync (bulk audio is), so **upload it
   separately** or it stays stranded on a partition macOS cannot read.
2. Re-transcribe on macOS (§5), then re-run `work/format_book.py` and
   `work/build_epub.py` to produce a better book 6.

**Do not start a competing recorder.** An earlier duplicate captured silence for
14 minutes while the real one worked.

---

## 4. Thread B — the engine (`~/Projects/blue-pencil`)

### The contamination question is settled, and the answer is good

`bp probe` ran against the live API. Opus, asked cold, said it knows the series
through book 4, is aware of book 5, and has **no knowledge of a sixth volume** —
zero events recalled, and it declined to invent rather than guess. Saved at
`eval/probe/bobiverse-The Infinite Extent (Bobiverse book 6).json`.

**Book 6 is uncontaminated. The backtest is valid.**

### Eight commits, all pushed

| Commit | What it fixes |
|---|---|
| `9c843c8` | EPUB ingest: text counted twice, drop caps splitting words, chapter heads not found, `book_id` breaking the SCUT lookup |
| `bfb4f55` | Epistemic check refuses to run when a channel's availability is unresolved, instead of passing everything silently |
| `cbe6726` | Model-backed calls brought to the current API — `thinking`, `temperature`, forced `tool_choice` |
| `dd28306` | Book 3's POVs recovered; a place catalogue replacing the six-entry pair file |
| `d11b85c` `69fa0b1` | The contamination probe was inverting its own verdict, and its test agreed with the bug |
| `f547fe5` | Scene breaks printed as ornaments rather than typed — 212 of them, invisible |
| `f2d3204` | Strict-schema fallback; extraction works |

Measured effect on the corpus:

```
words        964,202 -> 523,382    (duplication removed)
scenes           661 -> 836        (ornament breaks found)
POV assigned      0% -> 96%
dated             0% -> 96%
placed            0% -> 61%
distinct POVs     63 -> 26         (44 of them were chapter titles)
SCUT relay   unresolved -> live from book 2
```

### The finding worth carrying forward

**Every bug was in reading messy input or in the model-backed calls. The
deterministic reasoning core — `knowledge.py`, `space.py`, `timeline.py` — had
none.** Five books, five typesetting conventions, and the parser only knew the
ones it was written against.

So when "should this just use an LLM?" comes back: the answer that fits the
evidence is **an LLM at ingest, determinism in the reasoning**. Ingest is a
one-time batchable pass over 359 chapters; the reasoning is where
reproducibility and auditability live — and Kris cannot read the code, so a
checker that silently passes is worse for him than one that loudly fails.

The deterministic half also proved its own case: every one of those bugs was
found by *noticing numbers that disagreed with each other*, and re-ingesting to
check a fix is free and takes seconds.

### Extraction: works, but two numbers are not what the plan assumed

`bp extract` produces good records — a 3-scene test gave 27 cited entities, none
rejected, and it marked a character mentioned only in passing as `unknown`
rather than asserting. Caching works (81% of input served from cache).

- **Cost**: measured **$0.040/scene** for one pass → ~$134 for 836 scenes × 4
  passes, **~$67 with `--batch`**, against the **$10** `bp cost` predicts.
  `bp cost` is a formula, not a measurement; trust the measurement.
- **Speed**: ~**40 s/scene** synchronously → ~37 hours for the full corpus.
  **`--batch` is mandatory, not merely cheaper** — it parallelises as well as
  halving the price.

**Unfinished:** a steady-state measurement on mid-corpus scenes (the sampled
ones were chapter-one, the most entity-dense in the book, so $67 is likely an
over-estimate). Two attempts died — one to a timeout set below my own estimate,
which discarded ~35 scenes of paid work because `extract()` commits at the end
of a pass; one to the harness reaping a background task. `/tmp/midsample.py`
holds the per-scene-commit version. **If retrying: do not pipe through `tail`**
(it buffers, so a killed run shows nothing), and set the timeout far above the
estimate. Or skip it and run `--batch` over everything.

---

## 5. Thread C — the macOS side

Kris dual-boots into macOS for two jobs Linux cannot do well:

1. **Apple-Silicon-native Whisper.** The Linux run was CPU-only because
   CTranslate2 (what faster-whisper sits on) has only CPU and CUDA backends —
   the M2 GPU is unreachable from that stack on any OS. On macOS use **MLX
   Whisper** (Apple's own framework, simplest) or **whisper.cpp + Core ML**.
   Keep `large-v3-turbo`, the hotword list in `work/hotwords.txt`, and the
   Bobiverse initial prompt.
2. **Apple Books annotations.** Kris reads the epub on his iPhone and highlights
   transcription errors. With iCloud sync those land on macOS at
   `~/Library/Containers/com.apple.iBooksX/Data/Documents/AEAnnotation/*.sqlite`
   — table `ZAEANNOTATION`, `ZANNOTATIONSELECTEDTEXT` is the highlight and
   `ZANNOTATIONNOTE` the note. Highlights are verbatim text from
   `chapters_formatted/`, so each one greps straight to its chapter. **Not
   reachable from Linux** — this machine has no macOS layer.

Two lessons from the Linux transcription that must carry over:

- **Do not cut audio at fixed intervals.** Chunking into rigid 1200 s windows
  (done to survive an OOM kill) cut mid-sentence and garbled several seams, and
  swallowed an entire chapter heading. Overlap the windows, or cut on silence.
- **Turn off `condition_on_previous_text`.** A hallucinated repeat fed itself
  back as context until it overflowed the model's window and crashed the run.

---

## 6. Next steps, in order

1. **Stop the 1.0x recording** when the book finishes, and upload that FLAC to
   Drive before rebooting.
2. **Settle the extraction cost**, or accept ~$67 and run
   `./bp-run extract --profile bobiverse --batch`. Set a spend cap on the key
   first.
3. **`bp audit -n 50`** — the first genuine human gate. Judging the *graph*, not
   prose, so it does not contaminate the backtest.
4. **`bp plan` → `bp draft` → `bp check` → `bp accept`** for the fan book 6,
   hands-off, ~$97.
5. **Score it**: `bp backtest --profile bobiverse --hide "book 6"`, Tiers A/B/C.
6. **Then book 7**, with as much human involvement as he wants.

## 7. Open gaps

- 20 scenes still have no POV; 319 unplaced. Parsing/data tails.
- Places marked `series?` in `profiles/data/bobiverse-places.csv` are inferences,
  not catalogue data — Poseidon, Quin, the Heaven's River interiors. A wrong
  distance makes the checker confidently wrong rather than silent.
- `travel_speed: 0.5c` predates the later books' faster drives, so a geography
  finding on a book 4–5 chapter may be calibration, not a real catch.
- Repetition fires 3 hard findings on Taylor's own prose against the plan's "≤2
  false alarms" bar. It compares one chapter to a whole-POV mean, so quiet
  chapters always look wrong. Now that 71 real chapters exist as ground truth,
  they are the obvious calibration set.

## 8. Working notes

- **Never run `secret-tool search`** — it prints secret values. That is how a
  key was exposed in this session and had to be rotated. Use
  `secret-tool lookup ... | wc -c` to check.
- `./bp-run <cmd>` fetches the key at launch and passes it to that one process.
- Gate commits on a green test run. A commit was pushed red once by chaining
  `git commit` after `pytest` without checking the exit code.
- 94 tests, offline, ~2 s. Everything deterministic runs with no API key.
- Kris vibe-coded this repo with an AI agent and **has not read the code**.
  Decide code-level questions rather than offering him a choice between
  implementation behaviours he has no basis to evaluate; tell him the
  consequence instead. Budget, taste and risk appetite are genuinely his.

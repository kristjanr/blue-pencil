# Cast before scenes

*One of three independent designs. Written without reading the other two.*

## The number that settles it

**One character entity out of 501 is cited in more than nine scenes.** Across five
novels. It is Riker, at eleven.

| scenes citing the entity | entities | share |
|---|---|---|
| 0–1 | 429 | 86% |
| 2–4 | 64 | 13% |
| 5–9 | 7 | 1% |
| 10+ | **1** | 0.2% |

The distribution is upside down. A five-book series has a few heavily-cited
people and a long tail of walk-ons; this graph has only the tail. No entity
accumulates citations, which means the extractor is not recognising returning
characters at all. It is minting new ones.

## Why: the prompt has never heard of anyone

Every extraction call gets the scene id, POV, date, place, and the series'
information channels (`_scene_prompt` in `bp/extract.py`). It gets **no list of
who already exists**. So when a later scene meets a character called Enoki, it
cannot know Enoki is Bob-1 — that was established in another book. It does the
only thing available: mints `enoki_bob1_alias` as a new person.

That id is the bug in miniature. The model knew enough to write "alias" in the
identifier, and the schema gave it nowhere to put that knowledge except a new row.

Forty rows currently sit in the same table as the protagonist:

    bob1                   Bob-1
    bob_offscreen          Bob                            — mentioned, not present
    bob_replicant_generic  Bob (the Bobiverse replicant)  — generic reference
    enoki_bob1_alias       Enoki                          — the id admits it
    icarus_pov             Icarus (Bob POV)
    third_bob_unnamed      Third Bob (unnamed)
    unnamed_survey_bobs    Two survey Bobs (unnamed)      — two people, one row
    bobs-employees         Bob's Employees                — not a character
    bob-clones-unbuilt     Bob clones (future/unbuilt)    — does not exist yet

Nothing in the schema distinguishes a person from a passing reference to one,
from a group, from a hypothetical.

## The second flaw: scene-local observation filed as corpus-scoped identity

Extraction runs per scene, so the description it writes is necessarily about
*this appearance*. The field it writes into is corpus-scoped. There is nowhere
else to put a scene-local observation, so it goes in the global field and the
last scene to run wins.

That is why descriptions "focus on the unimportant" — they are not descriptions
of the person. One reads: *"Referenced as having an android that emulated a
Deltan; not physically present in scene."* Nothing there is a fact about the
character.

Measured across all 501 character entities:

| pathology | count | share |
|---|---|---|
| description describes the scene, not the person | 111 | 22% |
| name is literally "unnamed" / "unidentified" | 79 | 16% |
| name denotes a group, not an individual | 23 | 5% |
| cited in one scene or none | 429 | 86% |

A third symptom has already cost work: `bp/export.py` needs a tiered resolver
with a most-cited tie-break to guess what a name in a column meant, because the
same column holds ids, display names and aliases. That resolver is not a
feature. It is a prosthesis for an extraction that never committed to an identity.

## The change

**A character is not something you can extract from a scene. It is something you
can only compute from a book.** So stop asking a scene-level call to do it, and
split the two jobs the current pass has fused: observing a reference, and
deciding who it refers to.

### Stage 0 — the agent reads the book and writes the roster *(0 API calls)*

Rule 2 taken literally and moved to the front. Before any spend, the agent reads
book 1 and writes a cast sheet: who exists, their aliases, one line of identity
each. Perhaps forty people for Bobiverse book 1.

This inverts the present arrangement. Today the software extracts and the agent
corrects afterwards, which is why correction is endless. Here the agent
establishes identity first and the software fills in detail against a fixed
roster. Correction becomes amendment of a small document, not archaeology over
501 rows.

### Stage 1 — find mentions deterministically *(0 API calls)*

Scan every scene for referring expressions: proper-noun candidates plus every
roster name and alias. Write to a `mentions` table — scene, surface form as it
appeared, offset, null binding. Tuned for recall; it is a candidate generator.
`bp/mentions.py` already works on this principle for the draft checks.

Nothing here decides anything. That is the point: free, deterministic,
re-runnable after any corpus repair.

### Stage 2 — bind each mention to the roster *(≈836 calls, ≈$4)*

Per scene, hand the model the roster and that scene's mentions with a window of
text around each. For every mention it returns one of three things: a roster id,
`not-a-person`, or `new`.

The shape of the answer is what fixes the table. **It cannot mint an entity,
because minting is not one of the moves.** `not-a-person` is where the 16% of
"unnamed" rows and the 5% of groups go instead of into the entity table. `new`
creates nothing either — it escalates to the agent, who adds to the roster or
rejects it. An uncertain model says so in a field rather than by creating a
duplicate.

### Stage 3 — describe each person once, from everywhere they appear *(≈40–80 calls, ≈$3–6)*

One call per roster character — not per scene — carrying every passage bound to
them. The only arrangement in which a description can be about the person: the
call sees the whole of their appearances, so it can tell what is characteristic
from what happened once.

Scene-local observations do not disappear, they stop being mislabelled. They
live on the mention, where "not physically present in this scene" is exactly the
right thing to record.

## Where the agent stands (rule 1)

A check between every stage, and — the part the current codebase gets wrong — **a
command behind every correction the check can prompt.**

The evidence is concrete. Most corrections to this graph have been made with
Python scripts issuing raw UPDATEs: the entity merges, the drop-cap repair, 142
restored demotions, 41 mis-settled promises. Only `bp resolve apply` has a
command. A system whose operator must bypass it to operate it has not been
designed for rule 2 — it has been designed for a user who does not exist.

- **After stage 1** — mentions per scene, and scenes with none. A scene with no
  mentions is either prose without people or a scanner failure; the agent must be
  shown which.
- **After stage 2** — every `new`, every `not-a-person`, and **every roster member
  bound in zero scenes.** That last check would have caught this entire class of
  defect on day one: a cast member nobody refers to means the binding failed, not
  that the character is absent.
- **After stage 3** — each description beside the passages it was written from, so
  the agent checks a claim against its evidence rather than against memory.

Every gate reports *what it examined*, not only what it found. A check that
passes over nothing must say so.

Commands rule 2 implies:

    bp cast                                    read/add/amend/alias the roster
    bp bind list --unbound --new --uncertain   what needs a decision
    bp bind set <mention> <entity>             with a change trail
    bp entity merge <a> <b>                    ditto
    bp entity describe <id> --show-evidence    the passages behind the text

## Cost

Roughly a wash, possibly cheaper, and **I would not argue for this design on
cost.** Stage 2 sends windows rather than whole scenes, which saves; stage 3 adds
a few dozen large calls, which spends it again. The argument is correctness: the
present design spends money to produce a table whose principal defect makes the
thesis ranking and the planner unreliable in ways invisible from their outputs.

## What survives, what goes

**Keep:** the citation discipline (every record names its scene; the export
refuses to ship prose); the promise ledger and two-stage settling, unaffected by
this; `bp/resolve.py`'s union-find candidate generation, which becomes the
mechanism behind `bp entity merge` rather than an after-the-fact cleanup; the
record-change trail.

**Throw away:** the `entities` pass as an extraction pass; `_merge_entities`'s
punctuation-collapse, a timid fix to a problem that stops existing; and the
export's tiered resolver. That last is the test of whether this design worked —
**if the resolver is still needed, it didn't.**

## Where this could be wrong

- **The roster may not be knowable in one reading.** A character introduced under
  a false identity in book 1 and revealed in book 3 breaks a roster fixed at book
  1. The roster must be versioned per book and amendable retroactively, with
  re-binding cheap enough to re-run. If it isn't cheap, this design has a hidden
  cost I have not priced.
- **Bobiverse is the adversarial case and my only sample.** A series where most
  characters are clones of one person with chosen names is close to worst-case for
  identity. Good stress test, bad sample of one.
- **It moves work onto the agent, which is the expensive part.** Reading a whole
  novel per book is not free in agent context even when free on the API line. If a
  book does not fit, stage 0 needs its own decomposition, which I have not designed.
- **I have not validated that binding is easier than extracting.** That a model
  given a roster will bind reliably is the load-bearing assumption and it is
  untested. It is cheap to test: twenty scenes with known cast, run stage 2 alone,
  count the bindings it gets right. That test should happen before any of this is
  built.

## Not in scope

The promise ledger has the same class of problem — no salience model, so a
series-ending stake sits in a flat list beside a thousand throwaways. Different
defect, deliberately not designed here.

---

*Figures measured against `graph/bobiverse.sqlite` on 18 September 2026. No book
text is reproduced.*

# Identity, argued from the consumer's end

One of three independent redesigns. This one is written from outside the engine: I have
spent this branch building viewers that read the graph, and every hard problem I hit was
the same problem wearing a different coat. The question I am designing against is
therefore narrow and testable:

> **What would the graph have to guarantee so that a consumer needs no resolver at all?**

Everything below follows from answering that honestly.

---

## 1. The diagnosis is not "not enough context". It is a design decision, half-executed.

Kris's read — the extractor lacked context to do the job — is right about the symptom and
understates the cause. The cause is in the source, stated outright. `bp/extract.py:51`,
in the system prompt every extraction pass carries:

> **"Prefer too many records over too few. A later pass merges duplicates."**

The extractor is not failing at deduplication. It was *instructed* to over-produce, on an
explicit promise that something downstream would reconcile. That downstream half was
never built: `resolve.py` has `adjudicate()` but no CLI, so, as `STATUS.md` records, "the
adjudicator can't run either." The 270 merges that have happened were driven by hand.

This matters because it changes what needs redesigning. A better prompt does not fix a
pipeline whose second stage is missing. **The duplicates are the bill for a deferred
payment, and the design question is who pays it and when — not how to phrase a request.**

Then there is the part no prompt can fix. Here is the entire context an entity extraction
call receives (`_scene_prompt`, `extract.py:130`):

```
SERIES · SCENE ID · POV · date · place · channels
TASK: Extract every character, faction, place, ship and organisation that appears…
       If a character is a copy of another, set parent_id and forked_on.
--- SCENE TEXT ---
```

No register of known entities. None. The model is asked to emit an `entity_id` for a
character without being shown whether that character already has one, and to set
`parent_id` — a foreign key — to a record it has never seen. **Inventing a fresh id is
the only behaviour available to it.** The wonder is not that we have 142 duplicate groups;
it is that we have only 142.

So the failure decomposes into two independent defects:

- **A missing reconciliation stage**, promised in the prompt and never built.
- **A question asked without its evidence**: identity is global, the call is scene-local.

## 2. The same defect, seen from where I sit

I never touched the extractor. I built readers, and hit this from the other side:

| What I hit | What it actually was |
|---|---|
| `events.participants` holds `"bob-3-bill"`, `"Garfield"` and `"Enoki"` in one array | extraction never committed to a reference type |
| The export needs a 4-tier `Resolver` with a most-cited tie-break | consumers re-deciding identity at read time |
| 2,834 mentions resolve to nothing; 1,254 distinct strings | references pointing at nothing, silently |
| 17 names shared between entities, 1,659 mentions tie-broken | one name, several records, no arbiter |
| Refusing to resolve ambiguity once discarded 7,630 mentions of the most-referenced character | the tie-break is load-bearing, not a nicety |
| 142 candidate groups, 372 records; 30.8% of cast entries name one | the deferred payment, measured |
| 269 of 2,219 report hops cannot resolve both endpoints | broadcasts have no single recipient — a *schema* gap |

`profile.canonical()` falls back to `name.strip()`, so a reference that matches nothing
resolves to itself and joins nothing — **failing silently, which is why this survived so
long.** Every consumer that joins on these columns is guessing, and two consumers guessing
independently can disagree without either knowing.

**The resolver is not a utility. It is a symptom.** It exists because identity is inferred
at read time instead of decided at write time. My design deletes the need for it.

## 3. The reframe: two jobs, fused, with different evidence requirements

Entity extraction as built does two things in one call:

**Job A — mention detection.** *This scene names someone called X, doing Y.* Local,
cheap, verifiable against the text in front of you, high recall achievable.

**Job B — identity assignment.** *This X is the same person as the X in chapter 3.*
Global, requires knowledge of everything extracted so far, expensive to get wrong in both
directions.

The current call is given the evidence for A and asked to also answer B. **More context
improves B but can never make it correct, because B's evidence is the whole corpus and
context is finite.** Book 5 chapter 60 cannot hold books 1–4. This is why I think "more
context per call" is a palliative rather than a fix, and why a redesign has to *separate*
the jobs rather than better-resource the fused one.

The insight that makes B tractable: **B does not need the corpus. It needs a register
derived from the corpus.** To decide whether "Hersch" is Herschel you need the list of
known entities with their aliases and a one-line disambiguator — a few hundred lines, not
five novels. Compression is what turns a global question into a locally answerable one.

## 4. The design

### 4.1 Mentions are primary and carry no identity

A new table, append-only:

```
mentions(mention_id, scene_id, surface_form, offset, role, quote, kind_guess)
```

The per-scene extraction pass's *only* job: who is named here, as spelled, doing what. It
is shown no entity list and never emits an id. A mention is a claim about the text — the
surface form is at that offset — so it is verifiable by `bp ground` in the way a claim
about identity never can be.

Recall comes from two sources that already exist: `ingest.harvest_names` gives a
deterministic capitalised-token floor, and the model adds what morphology misses ("the
badger", "His Badgerness") and removes what it over-catches ("Okay", "Yeah"). **This is
Kris's third idea and I think it is his strongest** — deterministic first pass, model to
validate. I would apply it here, at the mention layer, rather than at the entity layer.

### 4.2 Identity is a separate pass that sees the register

For each mention not already bound, one question, asked with the register in hand:

```
resolutions(mention_id, entity_id | NEW | UNCERTAIN, basis, decided_by, decided_at, confidence)
```

Three outcomes and no others. `UNCERTAIN` is a first-class answer, not a failure — it is
the queue the agent works. `basis` records *why*: the text stated it, the alias matched,
the scene co-occurrence implied it.

The register handed to this pass is scoped — entities of the relevant kind, recently seen
or frequently cited — so it stays small as the corpus grows. This pass runs incrementally:
per book, or per chapter, in reading order, so identity is decided in the same order a
reader would decide it.

### 4.3 Entities are a projection, not a written record

This is the piece I would argue hardest for, and it is where I expect to differ most from
the other two designs.

**An entity is the set of mentions resolved to it.** Not a row someone wrote. That single
change buys four things the current model cannot do:

1. **Merging is one decision record**, not a destructive rewrite of every referencing row.
   Today a merge rewrites `citations`, `participants`, `observed_by`, `threads.characters`,
   `objects.holder`, `parent_id`, and leaves a tombstone in `entity_merges`.
2. **Splitting becomes possible at all.** Today it is not. Merges are one-way, and the
   `Guppy` (one GUPPI per replicant) and `Survey Drone` (several real drones) cases need
   exactly this. A wrong merge is currently unrecoverable without a backup; under a
   projection it is one flipped decision.
3. **Descriptions are written once the mention set is known**, not from the first scene the
   character appeared in. This is Kris's "descriptions focus on the unimportant" complaint,
   and it has the same root: the description of Bob Johansson was written by a model that
   had seen him in exactly one scene. A description generated from all ~300 of his mentions
   is about what he does across a book. **Here is where I would put Kris's per-character
   agent** — description quality through focus is real; identity through focus is not,
   because an agent that only sees one character cannot notice it is describing someone
   another agent already has.
4. **Provenance is inherent.** "Why is this one entity?" is answerable by listing the
   decisions, each with its basis and its author.

### 4.4 References are ids, and the write path enforces it

`events.participants`, `reports.sender`/`recipient`, `threads.characters`,
`objects.holder` hold entity ids — never surface forms — with the raw string kept
alongside, the way `scenes.cast_raw` already does and which was the right instinct.

`db.py` already refuses to write a claim it cannot cite:

> *"The write path enforces the invariant the whole design rests on: a claim with no
> citation does not get written."*

Extend exactly that, in the same place and the same style:

> **A claim whose references do not resolve does not get written.**

Unresolvable references become `UNCERTAIN` mentions in the queue instead of silently
joining nothing. **That is the guarantee that makes a resolver unnecessary** — not a
convention, an invariant the database enforces.

### 4.5 Two things the schema must learn to say

Both surfaced from consuming the graph, and neither is an identity question, so no
adjudicator can settle them. They will keep arriving as unanswerable merge questions until
the schema can express them:

- **Same type, different instance.** Six `Guppy` records, one GUPPI per replicant. Not
  duplicates, not unrelated. Needs an `instance_of` relation.
- **A collective and its members.** `Bobiverse` / `the Bobs` / `BobNet` grouped with ten
  members; 269 report hops address a crowd ("assembled Bobs", "moot attendees") that has no
  single recipient entity. Needs a group kind with membership, and reports that may address
  one.

## 5. Where the agent fits, given the ground rules

Kris's rules 1 and 2 — each substep checked by an agent, the agent reads the whole book and
fixes issues through the software's own tools. The design above gives that agent **typed
work with a queue**, rather than an open invitation to fix the graph:

| After | The agent's job | Bounded by |
|---|---|---|
| Mentions | Sample scenes, name who the mention table missed | recall against the text |
| Identity | Work the `UNCERTAIN` queue and audit the `NEW` decisions | the queue's length |
| Projection | Read a description against its mention set; rewrite when it misrepresents | one entity at a time |

The distinction I would hold to: **the agent's output is decision records, not edits.**
Every call it makes is stored with its basis and is individually reversible. An agent that
edits the graph directly is indistinguishable from the extraction we are replacing — it
would be a second unaudited writer. The tools it is given should be `resolve`, `split`,
`describe`, `defer` — each writing a row, none mutating in place.

This also makes the agent *measurable*, which rule 1 needs to mean anything: sample its
decisions, score them, and you have a number. `eval/entity_duplicate_verdicts.yaml` is
already the right shape for that — 32 groups with reasoning, including three findings no
automated pass would have got (Frieda is stated distinct in the text; Spike and Guppy are
an instance question; Kiroshi and Richards are contradictory descriptions rather than
identity questions).

## 6. Kris's own hypotheses, assessed

He flagged these as unvalidated, so here is my honest read rather than agreement:

- **"Maybe just a good enough prompt."** I do not think this holds. The prompt cannot
  reference entities it is not shown, and the call is not shown any. This is the cheapest
  thing to *test*, though, and it would take one book to falsify — worth doing first purely
  to settle it.
- **"More context in each call."** Helps, bounded, does not scale. Improves Job B without
  making it correct, and raises per-scene cost across 836 scenes.
- **"Deterministic first pass, then Claude to validate/merge/describe."** The strongest of
  the four. My §4.1 is this idea, moved to the mention layer where the deterministic pass
  is genuinely reliable.
- **"An agent per important character."** Right for descriptions, wrong for identity, for
  the reason in §4.3 — per-character focus cannot detect cross-character duplication.
- **"Might widen beyond characters."** It must. 36 of the 142 groups are factions and 36
  are places; the `Brazilian probes` group has 8 records. Characters are the expensive case
  because only they move through scenes and strand the geography checker, but the defect is
  general.

## 7. Cost, risk, and what I would not claim

**This is re-resolution, not re-extraction.** Mentions can be derived from existing
citations plus scene text; the 2,095 entities become the starting register and the 142
candidate groups the first queue. The ~$58 already spent on extraction is not written off.
That is the main reason I would argue for this shape over a clean rebuild.

**Costs and risks, stated plainly:**

- Two passes where there was one. Mitigated: mention detection is small-model work, and the
  identity pass runs per *unbound mention*, not per scene, so it shrinks as the register
  fills.
- The register must stay small. Fine for ~500 characters; needs scoping by kind and recency
  for 2,095 entities.
- A projection-based entity table is a larger schema change than merge-in-place, and it
  touches every consumer including both viewers I have built. I would rather say that than
  discover it later.
- **The biggest risk is that this design is prettier than it is necessary.** If a register
  in the prompt plus the existing `adjudicate()` wired to a CLI fixes 90% of it, that is a
  far smaller change than §4.3 and it should win. I would test that before building this.

## 8. How we would know it worked

Not opinion — the numbers I have been measuring, run again:

| Measure | Now | Target |
|---|---|---|
| Cast entries naming a record in a duplicate group | 30.8% | under 5% |
| Mentions resolving to nothing | 2,834 | 0 by construction — they become `UNCERTAIN` |
| Names needing a most-cited tie-break | 17 names, 1,659 mentions | 0; ties become queue items |
| Resolver tiers needed by a consumer | 4 | 0 |
| Report hops resolving both endpoints | 1,950 / 2,219 | all, once groups can be addressed |
| Duplicate groups never adjudicated | 130 of 142 | 0 — the queue is the work list |

The first row is the one that matters, because it is what gates step 1 of the evaluation
ladder: a split character is invisible in half his scenes, `last_placement()` returns a
stale position, and the geography checker raises a false alarm with full confidence. The
whole point of fixing identity is that a false-alarm count becomes trustworthy.

---

*Written without seeing the other two designs, deliberately.*

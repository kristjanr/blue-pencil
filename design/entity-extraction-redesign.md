# Redesigning entity extraction

*A design from the session that reads the books and rules on the records. One of
three; deliberately written without consulting the other two.*

Kris's complaint is that character descriptions focus on the unimportant, are
sometimes plainly wrong, and that one character becomes several entities. All
three are true. They are also all the same bug, and it is not a prompt bug.

## What the graph actually contains

Measured tonight against `graph/bobiverse.sqlite`, 836 scenes, five books:

| | |
|---|---|
| character entities | 501 |
| cited in exactly one scene | **428 (85%)** |
| cited in five or more scenes | **8** |
| median description length | 122 characters |

A five-book series has perhaps fifty characters who matter. We have 501 records,
eight of which have enough attestation to describe anyone. The graph does not
contain characters. It contains 501 scene-local impressions, 428 of them seen
once and never revisited.

Then the part that should end the argument about prompts:

| character | scenes they narrate | scenes citing their entity record |
|---|---|---|
| Bob-1 | 298 | **4** |
| Bill | 120 | **7** |
| Riker | 88 | **11** |

The correlation is inverse and nearly perfect. **The more central a character is,
the less the extractor knows about them.** In first-person narration the narrator
is "I" — never named, never "appearing" as someone a scene introduces. Asked to
"extract every character that appears," a model reading one scene records the
people the narrator *talks to*. The protagonist is the one person in the room it
cannot see.

So the story bible knows most about spear-carriers and least about Bob. That is
Kris's "focuses on the unimportant", and no rewording of the prompt touches it.

Here is the most-cited character in the entire graph, in full:

> **Riker** — *A Bob who has renamed himself from 'Riker' to 'Will' to distance
> from the Star Trek naming convention. Appears at max VR capacity before joining
> Herschel and Neil's VR; reports on the Others' fleet and evacuation plans,
> including stasis pod shortages.*

Riker holds Sol while Earth dies, fights the Brazilian probe, loses Homer, and
spends five books as the Bob who stayed. None of that is here. What is here is
one late scene. And Theresa Sykorski, a character whose death and upload is a
turning point, is described as: *"Present at the gathering, eats a fish fillet,
comments wryly on someone's fate."*

That is not a model writing badly. **That is the correct answer to the question
it was asked.** It was shown one scene and asked what this person is. It said
what it saw. A model cannot write a character from a scene it has not read, and
the current design never lets it read one.

## The one rule

> **Never ask a model a question whose answer is not in the context you gave it.**

Every failure above is a violation of it, and the violations are structural:

1. **`description` is a corpus-level fact asked as a per-scene question.** The
   answer is not in the context. The model confabulates a summary of the scene
   instead, because that is the only true thing it can say.

2. **Identity is decided 836 times independently.** Each scene invents an
   `entity_id` with no knowledge of the other 835. The system prompt makes this
   explicit — *"Prefer too many records over too few. A later pass merges
   duplicates."* The split-character failure is not a defect of the design, it is
   the design, with cleanup deferred to a pass that cannot see the text either.

3. **`write_entity` is `INSERT OR REPLACE`.** The last scene to mention a
   character overwrites everything known about them with its own partial view.
   Accumulated knowledge is not merged, it is destroyed. And when `resolve`
   merges duplicates it keeps `max(description, key=len)` — selecting the
   *longest*, which selects for verbosity, not for truth.

4. **The POV tag is already in the database and extraction ignores it.** `scenes.pov`
   says Bob-1 narrates 298 scenes. That is free, deterministic, already ingested,
   and would have fixed the coverage inversion at zero cost.

## The design

Split the entity pipeline by *what kind of fact each thing is*, and ask each
question at the level where its answer actually lives.

### Layer A — Mentions. Per scene, deterministic, free.

For every scene, record every surface form that refers to a person: names and
aliases by string match, plus **the POV tag**, plus speaker attributions in
dialogue. Output is a `mentions` table of (scene, surface form, position). No
model. No identity claim. No description. Just: *this string, here.*

This is exhaustive and verifiable, and it is the layer that is currently missing
entirely. It also immediately fixes the inversion: Bob-1 gets 298 mentions
because the POV column says so.

The codebase already contains this exact two-layer pattern — `bp/mentions.py`
does lexical recall tuned for recall, then optional cheap-model precision, and it
is explicitly documented as never allowed to require an API key. It was built for
checking drafts. It belongs on the extraction side too.

### Layer B — Identity. Corpus-level, agent-run, narrow model calls.

Cluster surface forms into people. The model is never asked the open question
"who is this?" It is asked only **discriminating** questions, with evidence from
both sides in context: *here are two clusters, here are three passages from each,
same person or not?*

That is a question whose answer is in the context. It is also the question I have
been answering by hand all week, and my verdicts on 32 groups are a ready-made
fixture to test it against — the design can be scored before it is trusted.

Rarity already works here and is already in `resolve.py`: an alias everyone
shares discriminates nobody. "Bob" is not evidence; "Bob-3" is.

### Layer C — Description. Per character, whole-corpus, one focused pass.

**One call per character, not per scene.** Gather every scene that mentions them
(Layer A), and write the description from the whole set. This is Kris's "separate
agent per character" idea, and it is the only arrangement in which a description
can be true, because it is the only one where the answer is in the context.

It also inverts the cost curve in our favour. Today we pay 836 scenes × 4 passes
for descriptions that are wrong. Layer C is ~50 calls for the characters that
matter — a dozen scenes of evidence each — and the long tail of 428 one-scene
walk-ons does not need a description at all. A name and a citation is the correct
record for a character who appears once. **Most of the 501 should never have had
a description written.**

## What this asks of the software (Kris's rules 1 and 2)

Rule 2 — the agent reads the whole book and fixes what it finds — makes the agent
*part of the system* rather than a user of it. The software has to be built for
that, and right now it is not.

Tonight established the gap precisely. I cleaned 41 bad promises with a Python
script that opened the database directly and issued raw `UPDATE`s. Same for the
entity merges, the drop-cap repair, the 142 restored demotions. `bp resolve apply`
is the only correction of mine that has a real command behind it. **Most writes to
this graph do not go through the software's write path**, which means every guard
built into that path protects nothing I do.

So the deliverable is not only a new extraction pass. It is a **correction API**:
a verb for every judgment the reading agent makes — merge, split, rename,
re-describe, retype, add and remove alias — each one recording to
`record_changes`, each one reversible, and each one refusing to act on evidence it
cannot verify. Rule 1's per-substep check needs the same thing: a substep can only
be checked if it can be *inspected and corrected* without SQL.

The honest test of this design: **the reading agent should never need to touch
sqlite.** If it does, the API is incomplete, and whatever it did by hand is
outside every guarantee the system makes.

## What to do with the existing graph

Keep the citations — they were sampled at 48/48 correct, and they are what Layer A
would rebuild anyway. Keep events, promises and threads; the ledger work stands
and is independently audited.

**Throw away every character description.** All 501. They are answers to a question
that should not have been asked, and repairing them individually costs more than
regenerating them from a design that can be right. Keep the names and aliases as
input to Layer B, not as conclusions.

## Why I would not simply improve the prompt

Kris lists a better prompt as possibly the cheapest fix. It is the cheapest, and
it cannot work, for a reason the measurements make concrete rather than
theoretical: Bob-1 narrates 298 scenes and is cited in 4. No instruction changes
what is in the context window. You cannot prompt a model into reading a book it
was not shown.

More context per call (his second idea) does work, and Layer C is that idea taken
to its limit — but aimed per *character* rather than per scene, because that is
the axis the missing information actually lies along.

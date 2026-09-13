# What the record types mean

You asked how to audit records whose categories were never explained. Fair. Here is the
whole vocabulary, taken from the schema the extractor actually writes against.

Read this first, then do the audit as **two separate questions** — that's the fix for the
confusion, and it's on me for merging them the first time.

---

## The two questions, kept apart

**Question 1 — does the quote support the claim?** No vocabulary needed. Read the claim,
read the line beneath it, ask whether that line is real evidence for that statement. This
is the question that matters most, and you can answer every one of the 50 without knowing
what a "promise" is.

**Question 2 — is this filed under the right type?** This needs the vocabulary below, and
it's a *softer* question. Get it wrong and the graph is still true, just filed oddly.

Your comment on claim 8 is Question 1 done exactly right: *"This sentence specifically
cannot be the basis of the claim but indeed the claim itself is true. I looked up that
part in the book."* The claim is true and the citation is bad. That's the failure the
audit exists to find — a true-sounding record the engine cannot actually trace back to
the page. **Mark that one fails.** A record that can't prove itself is a record the
engine will later reason from without evidence.

---

## Entity

A character, faction, place, organisation or ship.

Carries `status` (alive / dead / missing / archived) and — because this series needs it —
`substrate`, the hardware a mind is running on, kept separate from where the body is.
`parent_id` and `forked_on` record who a copy diverged from.

*Audit it by asking:* does the cited line show this thing exists and is what the record says?

---

## Event

The fundamental object of the graph. Not a fact — an event, **plus the paths by which
knowledge of it travelled**: who observed it, who reported it to whom over which channel,
and what each character believes about it afterwards.

That last part is the whole point. It's what lets a checker ask *is there an information
path from this event to this character?* rather than merely *is this true?*

**Yes, a character reasoning or describing a plan counts.** You flagged this twice (#3,
#12) and the instinct is good, so here's the reasoning: when Hugh describes a strategy,
that is a thing that happened at a time, that others heard, and that changes what they
believe. The graph needs it, because a later chapter where someone acts on that plan has
to be checkable against when they could have heard it. What you should judge is whether
the event is *recorded accurately*, not whether it feels eventful — `significance` is a
separate field for that.

---

## Object

Chekhov's inventory: named significant items, where they are, who holds them.

Exists so a checker can catch a sword being in two places. Your comment on #7 —
*"not sure why this is a specific object"* — is a good catch: `Bill's recent inventions
(cargo, Howard's ship)` is a *bundle*, not an object. Nothing can track the location of a
bundle. **That one fails**, on Question 2.

---

## Promise — the confusing one

**A promise the text makes to the reader, not a promise a character makes.**

`prophecy · vow · foreshadow · setup · threat · question`

A dangling setup, an unanswered question, a threat left hanging. The raw material for
invention: a good twist cashes a promise the text already made. This is the machinery
that will let book 7 pay off things books 1–6 planted.

So the ones you queried are filed correctly:

- **#26** Jacques's surveillance → `setup`, planted and expecting payoff
- **#32** whether the Resistance hides tech elsewhere → `question`, explicitly left open
- **#21** the untranslated Roanokian script → `question`, an acknowledged uncertainty
- **#50** Bob's arrangement through the barkeep's cousin → `setup`

---

## Thread

A plotline with its last known state. The book outline is a schedule for advancing these.

Your comment on #2 is sharp: the thread is *named* "The Starfleet Thing" and the cited
line is about some unspecified "it" going sour. **That fails** — not because the thread
is fictional, but because a thread whose name is a vague gesture and whose state is
unstated cannot be advanced by a planner. A thread needs to say what it *is*.

---

## Report and Belief — inside events

A **report** is one hop of information: who told whom, over what channel, when it left
and when it lands. Arrival is normally *computed* from the series' space model, not taken
from the model — that's the trick that makes the epistemic checker deterministic.

A **belief** is what one character holds true about one event as of a date:
`knows · unaware · believes_false · suspects · misinformed`. False beliefs are recorded
deliberately; in a series built on unreliable narrators they are often the story.

---

## What I'd like from the second pass

Just **Question 1**, on all 50: does the cited line support the claim? Ignore the
categories entirely.

If you also want to flag a type that looks wrong, say so — but it's a bonus, not the job.

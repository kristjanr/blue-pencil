# Are the citations attached to the right character?

**2026-09-16.** Prompted by the Bob Johansson merge: reading its citations one by
one, three of nine turned out to be about entirely different people. That raised
the question of whether citation attribution is broadly untrustworthy.

**It is not.** The problem is real, small, and concentrated — and Bob Johansson was
the worst case in the corpus rather than a representative one.

## Sample: six of the most-cited characters, every citation read

| entity | citations | mis-attributed |
|---|---|---|
| Riker / Will | 13 | 0 |
| Bill (Bob-3) | 9 | 0 |
| Bridget Sheehy | 5 | 0 |
| George Butterworth | 9 | 0 |
| Theresa Sykorski | 7 | 0 |
| ANEC-23 | 5 | 0 |
| **total** | **48** | **0** |

One looked wrong and was not: `riker_will` cites book 4.21.4 for a line about
searching the Skippies' database, in a Bob-1 POV scene with Bill and Garfield also
speaking. Reading the scene, the line ends `…" Will said.` Correct.

## Why Bob Johansson failed where these did not

Attribution tracks **name ambiguity**, not extraction quality in general.

```
2,095 entities · 2,864 entity citations

name unique to one record   1,905 entities (90.9%)   2,532 citations (88.4%)
name shared by 2-3 records    167 entities ( 8.0%)     277 citations ( 9.7%)
name shared by 4+ records      23 entities ( 1.1%)      55 citations ( 1.9%)
```

"Bob" is claimed by **12** separate records. It is also the name of nearly every
character in the series. An extractor keying on a surface name has nothing to work
with, so it files whatever it sees — Garfield naming the Bobiverse, Howard on a
date, Garfield addressing Bill.

Butterworth, Sheehy, Theresa, ANEC and Jeeves are each claimed by one record, and
all 48 of their citations are right.

## What to do about the 1.9%

Nothing automatic. The worst offender, `bob_offscreen` (name: "Bob", 5 citations),
is a catch-all the extractor made for off-screen mentions. Reading them, all five
look like **Bob-1** — the text to "Will, Bill, and Bob", the moot-hall arrivals,
"Bob's work at Delta Eridani". But "Bob" is the least discriminating token in the
corpus and the standing rule is to merge only on positive evidence, so they stay
where they are and are flagged here instead.

`Guppy` (×3 records) and `Jeeves` are the same-type-different-instance case again:
every Bob has one, and the citations span five different Bobs' instances. Those are
not attribution errors; they are a relation the schema cannot express.

## The rule this suggests

A citation's reliability is a function of how discriminating the entity's name is —
the same principle already used in `bp ground` (a rare term is evidence, a common
one is not) and in `resolve.py`'s alias-rarity cap. It would be cheap to surface:
**an entity whose name is claimed by several records should carry lower confidence
on every claim built from it**, rather than being trusted equally with a Butterworth.

# A GUI for looking at the graph

You asked for a GUI to view "the relationships the extract phase builds". Two things
found while reading the schema change the shape of the answer, so they come first.

---

## Finding 1 — the `relationships` table is dead

`db.py` declares it, `models.py:166` declares the `Relationship` model, `db.py:687`
declares `write_relationship()`. Nothing calls it. Not `extract.py`, not `accept.py`,
not `resolve.py` — the only hits across the whole tree are the definitions themselves.

So the table has always held zero rows, and `bp graph stats` has always printed
`relationships 0` without anyone reading it as a defect. A GUI pointed at that table
would render an empty canvas and look like it was working — the exact failure `db.py`
warns about in its own comment about creating graphs on demand: *"an empty graph answers
every question plausibly and wrongly."*

This is worth deciding deliberately, and it is a separate decision from the GUI:

- **Delete it**, and treat relations as derived (what this plan assumes), or
- **Populate it** — add a relationship pass to extraction, at real token cost, to get
  edges the text states but no other table records: *owes a debt to*, *swore an oath to*,
  *suspects*. Those are not recoverable by derivation.

The GUI does not need this resolved to be built. It does need it resolved before anyone
concludes from the GUI that two characters are unrelated.

## Finding 2 — the real relations have six shapes

The extract phase does build a great deal of relational structure. It is just spread
across tables, mostly as JSON arrays:

| Relation | Where it lives | Its natural shape |
|---|---|---|
| Containment | `books` → `scenes` → `chunks`, `ord` | tree / linear spine |
| Provenance | `citations(record_kind, record_id, scene_id, quote)` | many-to-many overlay on the spine |
| Clone lineage | `entities.parent_id`, `forked_on`, `fork_day` | **forest on a time axis** |
| Co-participation | `events.participants`, `observed_by` | **weighted force graph** |
| Causation | `event_edges(src, dst, 'causes')` | **layered DAG** |
| Information flow | `events` → `reports(sender, recipient, channel, depart_day, arrive_day)` | **temporal path / sequence** |
| Epistemic state | `beliefs(character, event_id, state, as_of_day)` | **matrix, not a graph** |
| Setup and payoff | `promises.planted_in` → `paid_in` | **arc diagram over the scene spine** |
| Custody | `objects.holder`, `location`, `as_of_day` | **per-object timeline** |
| Plot state | `threads.characters`, `last_scene`, `status` | braid / storyline |
| Resolution history | `entity_merges`, `record_changes` | audit list |
| Irreconcilable readings | `contradictions.cites_a` / `cites_b` | side-by-side pairs |

Four of these are genuinely not graphs. Beliefs are a matrix. Promises are arcs over a
line. Object custody is a timeline. Contradictions are pairs. Drawing them as node-link
diagrams would actively hide what they say.

---

## Finding 3 — the join is ambiguous, and that is the most valuable thing to show

To draw an edge from an event to an entity you must join `events.participants` against
`entities`. The codebase does not agree on what is in that array.

- `accept.py:175` writes `profile.canonical(c)` — canonical **names**.
- `knowledge.py:115` reads it through `_canon()` — treating entries as **names** to
  alias-resolve.
- `extract.py:602` rewrites entries matching `"{old_id}"` during a merge — treating
  entries as **entity ids**.
- `tests/fixtures/synthetic.py:186` sets `entity_id=eid, name=eid`, making id and name
  identical — so the fixture cannot catch the disagreement.

`profile.canonical()` falls back to `name.strip()` for anything it does not recognise,
so a mismatched entry does not raise; it silently resolves to itself and matches nothing.
`CastRebuild.render()` already has a line for `"participants naming no known entity
(dropped)"`, which says this was seen before.

This is the same class of defect as the bootstrap-cast bug recorded in `extract.py`: a
join that silently loses rows, feeding a checker that then reasons confidently from less
data than it appears to have. **A viewer that drops unjoinable participants would inherit
the bug and make it invisible.** So the viewer counts them and shows them instead — which
is the argument for building this at all beyond pleasure: it is an instrument, not a
picture.

---

## What to build

A **local, read-only web app**: a stdlib HTTP server over the SQLite file, serving JSON
to a vanilla-JS frontend. Six views, because there are six shapes.

### Why this architecture

**Why read-only, physically.** `STATUS.md` says to run `backup.sh` before anything that
mutates the graph. A viewer that opens the database with a `file:...?mode=ro` URI *cannot*
mutate it — no discipline required, no backup ritual, and safe to leave open while an
extraction is writing, since WAL permits concurrent readers. The guarantee comes from the
connection string, not from being careful.

**Why a server rather than a static HTML export.** `review.py` already sets a precedent for
self-contained HTML, and for most views an export would do. But the highest-value view
renders `KnowledgeGraph.earliest_knowledge()` — a real piece of Python reasoning over
light-lag, channel availability windows and clone-fork inheritance. Reimplementing that in
JS would mean two copies of the epistemic rules that could disagree, which is precisely the
kind of drift the checkers exist to catch. The view must call the real function. A static
snapshot export is worth adding later for sharing, but it cannot be the primary form.

**Why no new dependencies for the backend.** `http.server` plus `sqlite3` plus `json` is
enough. The project currently has five dependencies and a house style of not adding magic.

**Frontend drawing — one decision to make.** The layouts needed are a force simulation, a
time-anchored tree, an arc diagram, a matrix and a layered DAG. Hand-rolled SVG is perhaps
400 lines and no dependency; vendoring `d3` v7 (~280KB, committed to `bp/viz/static/`) is
zero lines and works offline but is a large binary-ish blob in git. **Recommendation:
vendor d3.** The arc, matrix and tree views are trivial without it, but a hand-rolled force
simulation that behaves well at 2,000 nodes is a genuine time sink for no benefit.

**Scale.** The real graph is 836 scenes, 2,137 entities, 6,229 events, 16,393 citations,
2,886 promises. That is small for SQLite and too large for any single canvas. Every view
is therefore filtered by default — top-N by degree, or scoped to one book, one entity, one
thread — and says what it is hiding. A view that silently truncates is worse than no view.

### The views

**View 0 — Health.** Row counts, plus the numbers nobody currently sees: participants
naming no known entity, citations naming a missing scene, `parent_id` pointing at a
deleted entity, `paid_in` naming an unknown scene, events with no citation, entities
reachable through `entity_merges` only. This is Finding 3 made into a dashboard, and it is
useful on day one with no diagram at all.

**View 1 — Entity explorer.** Force-directed co-participation graph derived from
`events.participants`, edge weight = number of shared events, filtered to top-N. Node
colour by `kind`, size by degree. Click an entity → its record, aliases, status,
substrate, citations, merge history. This is the nearest thing to what "relationships"
would have meant, and it needs no model calls to build.

**View 2 — Lineage.** `parent_id` / `fork_day` drawn as a forest on a horizontal time
axis, so a fork's position is *when* it happened. Specific to this series and exact —
no inference. Directly supports the entity-duplication work in `STATUS.md`, because two
entities that ought to be one are usually adjacent in this tree.

**View 3 — Information path.** Pick an event and a character; render the `Hop` list from
`KnowledgeGraph.earliest_knowledge()` as a sequence over a time axis: observation,
each report hop with channel and whether arrival was computed or stated, fork inheritance,
and the arrival date. `knowledge.explain()` already produces this as a string — this is
the same content as a picture, plus the counterfactual: who *else* could know by day D
(`who_could_know`). **Highest value of the six.** It turns an epistemic verdict into
something a human can check at a glance, which is exactly what step 1 of the ladder
needs when counting false alarms.

**View 4 — Promise ledger.** Arc diagram over the linear scene spine: an arc from
`planted_in` to `paid_in`, height by `weight`, colour by `status`. Open promises are arcs
that leave the plant and land nowhere — the right visual metaphor for a dangling setup,
and a direct read on which ones book 7 could cash. 2,886 arcs needs filtering by weight.

**View 5 — Provenance.** Every record in every other view links here: record → its
citations → the scene text with the quote highlighted in place, plus book, chapter, POV
and date. This is the spine of the whole app. It is also the audit tool — the record-vs-
quote question `docs/record-types.md` calls Question 1 — done as clicking rather than as a
sampled markdown file.

**View 6 — Belief matrix and contradictions.** Characters × events heatmap coloured by
`state` (`knows` / `unaware` / `believes_false` / `suspects` / `misinformed`), scoped to
one event cluster or one character at a time. False beliefs are the interesting cells.
Contradictions render as side-by-side readings with both citation sets.

Deferred, worth naming: causal DAG over `event_edges` (currently only written when the
extractor emits `causes`, so probably sparse — View 0 will report how sparse, and that
number decides whether the view is worth building), object custody timelines, and thread
braids (1,967 threads needs a severe filter to mean anything).

---

## Phasing

Each phase is independently useful; none is a prerequisite for shipping the one before it.

**Phase 0 — `bp/viz/query.py`, no UI.** Read-only query layer plus the integrity and join
audit, exposed as `bp viz audit`. Delivers Finding 3 as real numbers against the real
graph. This is the phase that might change what the rest should be, so it goes first.

**Phase 1 — server, shell, View 0 and View 5.** `bp viz serve --port 8900`, binding
`127.0.0.1` only. The spine and the dashboard.

**Phase 2 — Views 1 and 2.** Entity graph and lineage.

**Phase 3 — View 3.** Information path, on top of the Phase 1 spine.

**Phase 4 — Views 4 and 6.** Promise arcs, belief matrix, contradictions.

## Testing

The synthetic fixture in `tests/fixtures/synthetic.py` is the development corpus: a world
whose every distance and information path is known exactly, so a wrong edge is provably
wrong rather than arguably wrong. Note that it sets `entity_id == name`, so it must be
extended with an entity whose id and name differ before it can test the Finding 3 join at
all. Query-layer tests assert edge counts against hand-computed expectations; the server
gets a smoke test per endpoint; no browser automation.

## Open questions for you

1. **`relationships`** — delete the dead table, or build the extraction pass that fills it?
2. **d3 vendored, or hand-rolled SVG?** Recommendation above is to vendor.
3. **Which view do you actually want first?** This plan orders by dependency, not by your
   need. If the information path (View 3) is what you want to look at, Phase 1 can be cut
   to the minimum that supports it.

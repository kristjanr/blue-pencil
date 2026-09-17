# A GUI for looking at the graph

You asked for a GUI to view "the relationships the extract phase builds". Four findings
change the shape of the answer, so they come first. Two came from reading the schema; two
came from reconciling it against `bp/export.py` (master, `c9ceb43`), which the engine
session published while this was being written and which is the contract this plan builds
against.

---

## Finding 1 — the `relationships` table is dead

`db.py` declares it, `models.py:166` declares the `Relationship` model, `db.py:687`
declares `write_relationship()`. Nothing calls it. The only hits across the whole tree
are the definitions themselves, so the table has always held zero rows and `bp graph
stats` has always printed `relationships 0` without anyone reading it as a defect.

**Resolved, better than the two options first offered here.** The engine session settled
it while this was being written: keep the table, derive every edge from the columns that
do hold relational data, and have the derivation record *how* each kind was built in an
`edge_kinds` block — so a viewer can show provenance rather than assert a fact the
database never stated. `declared` is wired up for the day something writes the table. The
original delete-or-populate framing was a false binary; this is the third answer.

Derived edge kinds, against the live graph:

| kind | from | directed | edges |
|---|---|---|---|
| `observed` | `events.observed_by` → `events.participants` | yes | 2,016 |
| `co_participant` | two entities in one `events.participants` | no | 1,466 |
| `shares_thread` | two entities in one `threads.characters` | no | 1,381 |
| `forked_from` | `entities.parent_id` | yes | 90 |
| `declared` | `relationships` | — | 0 |

Edges are aggregated, not repeated: a pair appearing in forty events is one edge with
`count: 40` plus up to eight `via` ids. Weight drives stroke width; it is not forty lines.
The heaviest edge in the corpus is Garfield—Bill at 421.

## Finding 2 — the real relations have six shapes

Unchanged, and the export confirms it. The structure extract builds is spread across
tables, mostly as JSON arrays:

| Relation | Where it lives | Its natural shape |
|---|---|---|
| Containment | `books` → `scenes` → `chunks`, `ord` | tree / linear spine |
| Provenance | `citations(record_kind, record_id, scene_id, quote)` | many-to-many overlay on the spine |
| Clone lineage | `entities.parent_id`, `forked_on`, `fork_day` | **forest on a time axis** |
| Co-participation | `events.participants`, `observed_by` | **weighted force graph** |
| Causation | `event_edges(src, dst, 'causes')` — 2,383 rows | **layered DAG** |
| Information flow | `reports(sender, recipient, channel, depart_day, arrive_day)` | **temporal path / sequence** |
| Epistemic state | `beliefs` — 8,154 rows | **matrix, not a graph** |
| Setup and payoff | `promises.planted_in` → `paid_in` — 2,886 | **arc diagram over the scene spine** |
| Custody | `objects.holder`, `location`, `as_of_day` — 881 | **per-object timeline** |
| Plot state | `threads.characters`, `last_scene` — 1,967 | braid / storyline |
| Resolution history | `entity_merges` (42), `record_changes` | audit list |
| Irreconcilable readings | `contradictions.cites_a` / `cites_b` | side-by-side pairs |

Four of these are not graphs. Beliefs are a matrix, promises are arcs over a line, custody
is a timeline, contradictions are pairs. Node-link diagrams would hide what they say.

## Finding 3 — the ambiguous join, now measured

The suspicion recorded in the first draft of this plan was right, and the export quantifies
it. `events.participants` holds entity ids, display names and aliases interchangeably —
`"bob-3-bill"`, `"Garfield"` and `"Enoki"` can sit in one array — because `accept.py:175`
writes canonical names, `extract.py:602` rewrites the same column as ids, and
`profile.canonical()` falls back to the input unchanged so a mismatch resolves to itself
and matches nothing. `tests/fixtures/synthetic.py:186` sets `entity_id == name`, so the
fixture cannot catch it.

Against the real graph:

- **2,551 mentions resolve to nothing**, across 1,174 distinct strings. Some are real
  characters never extracted (`Steven Gilligan`, `Will Riker`); some are collective nouns
  that should never be nodes (`moot attendees`, `the Others`).
- **17 names are shared by several entities** — six Guppys, three Alexanders — covering
  1,492 mentions, tie-broken to the most-cited candidate with every rejected candidate
  kept in `data_quality.ambiguous_names`.
- **1,587 of 2,095 entities have no edge at all** — mostly places, factions and ships
  named in prose but never in an event cast. That is real extraction coverage, not an
  export artifact.

The resolver's tier order — exact id, unique name, unique alias, then most-cited wins —
earns its keep on one case: `Bob-1` is both the first replicant's name and an alias of a
different POV entity, and refusing to resolve ambiguous names discarded **7,630 mentions
of the most-referenced character in the series**. A visible, overridable tie-break beats
losing a third of the graph quietly. The viewer's job is to keep it visible: mark edges
whose endpoint came from that table, and show `scenes[].cast_raw` beside the resolved
`cast` so the reader sees what the text said as well as what it matched to.

## Finding 4 — what the export cannot answer, and why it matters

Verified twice: against the two published JSON files, and against `bp/export.py` itself,
which reads eleven tables and names neither of the first two below.
`detail.json` carries `events` (6,229), `event_edges` (2,383), `beliefs` (8,154),
`promises` (2,886) and `citations` (16,390). Three things are absent:

- **`reports` is not exported at all.** Those are the hops — sender, recipient, channel,
  `depart_day`, `arrive_day`. Beliefs are the *outcome* of information travelling;
  reports are the *route*. Without them the information-path view cannot be built from
  the export, and it is the most valuable view in this plan.
- **`contradictions` is in neither file.** Currently 0 open, so nothing is lost today,
  but the view has no data source.
- **No citation quotes and no scene prose**, deliberately. So the provenance drill-down —
  the record-versus-quote check `docs/record-types.md` calls Question 1 — cannot run off
  the export by design.

This is not a complaint about the export; it is what decides the architecture below.

## What to build

Findings 3 and 4 draw the line for us. The viewer splits into two tiers, and the split is
not a preference — it falls exactly along *does this need book text or the epistemic
engine?*

**Tier A — static, shareable, no database.** Entity graph, lineage, promise arcs, causal
DAG, belief matrix, coverage. Everything here is answerable from `graph.json` and
`detail.json`. It needs no server, no SQLite and no API key, so it can be published and
handed to someone the way the engine session's own handover page already was.

**Tier B — local only, needs the database.** The information path (needs `reports`, which
the export omits, and `KnowledgeGraph.earliest_knowledge()`, which is real Python) and the
provenance drill-down (needs scene prose and citation quotes, excluded by design). These
stay behind `bp viz serve` on the machine that holds `graph/bobiverse.sqlite`.

### Why this shape

**Why build Tier A against the exported JSON rather than the database.** Not because the
database is awkward, but because the export is a *contract*. `edge_kinds`, `data_quality`
and `source` are a documented shape that a re-export drops straight into; anything built
against columns incidental to one snapshot breaks the next time extraction runs. It also
means the viewer runs where the corpus is not — no book text is in either file, so Tier A
can be shared without shipping five novels.

**Why Tier B cannot be folded in.** The information path is light-lag arithmetic, channel
availability windows and clone-fork inheritance. Reimplementing it in JS would mean two
copies of the epistemic rules that can silently disagree — the exact drift the checkers
exist to catch. The view must call the real function, so it must have Python and the
database. That is a boundary, not an inconvenience.

**Read-only, physically.** `STATUS.md` says to run `backup.sh` before anything that mutates
the graph. Tier B opens the database with a `file:...?mode=ro` URI, so it *cannot* mutate
it — no discipline required, and safe to leave open while an extraction is writing, since
WAL permits concurrent readers. The guarantee comes from the connection string, not from
being careful. Tier A cannot write anything at all.

**Scale, and the honesty rule.** 2,095 entities, 4,953 edges, 6,229 events, 2,886
promises. Small for a machine, far too large for one canvas. Every view filters by
default — top-N by weight, or scoped to a book, an entity, a thread — and **states what it
is hiding**. Specifically: the 1,587 entities with no edge get an explicit "mentioned
only" shelf rather than being dropped, because a graph that silently omits three quarters
of its nodes is a graph that lies about coverage.

**Drawing claims differently.** Every record carries `claim_type` (`explicit` = the text
said it, `inferred` = the model concluded it) and `confidence`. These are different claims
and must not render identically — inferred edges dashed, low confidence lighter. Likewise
events carry `day_lo`/`day_hi`, not a date: any timeline draws the **span**, never a point.
An edge whose endpoint came from `ambiguous_names` gets marked too.

**Frontend drawing — one decision to make.** Hand-rolled SVG is perhaps 400 lines and no
dependency; vendoring `d3` v7 (~280KB) is zero lines and works offline. **Recommendation:
vendor d3.** Arc, matrix and tree views are trivial without it, but a force simulation
that behaves well at 2,000 nodes is a genuine time sink for no benefit.

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

Phase 0 of the first draft — build a read-only query layer and a join audit — is **done
and should not be rebuilt.** `bp/export.py` is that layer, and `data_quality` is that
audit. What remains is the drawing, and one repair.

**Phase 1 — Tier A shell, View 0 and View 1.** Coverage dashboard and the entity graph,
off `graph.json` alone. This is the shortest path to something worth looking at, and it
needs nothing that does not already exist.

**Phase 2 — Views 2 and 4.** Lineage forest and the promise arc diagram. `forked_from` is
only 90 edges, so the lineage view is exact, cheap and immediately legible — the best
value-per-line in the plan.

**Phase 3 — View 6 and the causal DAG,** off `detail.json`. Belief matrix and the 2,383
causal edges.

**Phase 4 — Tier B: `bp viz serve`,** the information path and the provenance drill-down.
Blocked on the export gap below, or on running against the database directly.

### The one repair this plan asks for

`reports` needs adding to `detail.json` — `event_id`, `sender`, `recipient`, `channel`,
`depart_day`, `arrive_day`, `computed`, `status`. Recipients and senders are entity
references, so they want the same `Resolver` pass every other reference gets. It carries no book text, so it does not
touch the no-corpus-text guard. `contradictions` is worth adding at the same time
(`cites_a`/`cites_b` are scene ids, not quotes). With reports present, the information
path moves out of Tier B and becomes shareable like everything else — which is the single
highest-value change available to either side of this work.

## Testing

`tests/fixtures/synthetic.py` remains the development corpus: a world whose every distance
and information path is known exactly, so a wrong edge is provably wrong rather than
arguably wrong. It sets `entity_id == name` and so must be extended with an entity whose
id and name differ before it can test the Finding 3 join at all. Tier A additionally gets
a fixture cut from the real export — a few hundred entities — so view tests run fast
without carrying 11.6 MB into the repo.

## Open questions for you

1. ~~Delete or populate `relationships`?~~ **Answered** — derive, keep the table, record
   provenance per edge kind. See Finding 1.
2. **d3 vendored, or hand-rolled SVG?** Recommendation above is to vendor.
3. **Which view first?** The phasing orders by dependency, not by need. If the information
   path is what you actually want to look at, that reorders everything and needs the
   export repair first.
4. **Where should Tier A live** — a published page like the engine session's handover, or
   a file in this repo served locally? The first is shareable; the second is version
   controlled. They are not exclusive.

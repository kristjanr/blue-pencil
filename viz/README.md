# The atlas, and the local half

Two viewers over one graph. The split is not a preference — it falls exactly along
*does this need book text or the epistemic engine?*

## `index.html` — the atlas (shareable, no database)

Six views, opening on **Duplicates** — one character recorded as several entities, the
defect that costs the most downstream. Then entity network, replicant lineage, promise
ledger, belief matrix, and a coverage dashboard that leads with what the extractor
*missed*. One HTML file, no build
step, no libraries. Everything it draws comes from the two files `bp export` writes,
fetched at runtime:

```
bp export --profile bobiverse --out ./viz
python3 viz/make_duplicates.py viz/graph.json > viz/duplicates.json
python3 -m http.server -d viz 8901     # then open http://127.0.0.1:8901
```

`make_duplicates.py` calls `bp.resolve.candidates` — the same function `bp resolve` uses —
against the exported entities, rather than reimplementing the matching rule in JavaScript
where it would drift. It overlays the recorded verdicts from
`eval/entity_duplicate_verdicts.yaml` and the merges already applied. **This belongs in
`bp export` eventually**; it lives here because the export does not emit it yet.

`graph.json` and `detail.json` are deliberately not committed — 12 MB of derived data with
a source of truth one command away, and `.gitignore` keeps them out.

## `bp viz` — the local half (needs the database)

```
bp viz --profile bobiverse             # http://127.0.0.1:8900
```

Serves two things the atlas cannot:

**The information path.** `bp export` now emits `reports`, so the hops themselves are in
`graph.json` as the `informed` edge kind. What is not in there is the question people
actually ask — *could this character know this yet?* That is derived from those hops plus
fork inheritance and channel availability windows, and it is answered by calling
`KnowledgeGraph.earliest_knowledge`, not by reimplementing it. Two copies of the epistemic
rules that can silently disagree is the drift the checkers exist to catch.

**Citation provenance.** The quote highlighted in the scene prose around it, with a flag
saying whether the quoted line is actually in the scene it names. The export carries no
book text by design, so this check can only happen where the corpus already is.

The connection is opened `mode=ro`. It cannot write — so it needs no `backup.sh`
beforehand, and is safe to leave open while an extraction is running.

## Why it looks like this

**Five views, not one graph.** The relations the extractor builds have different shapes,
and most are not node-link graphs: beliefs are a matrix, promises are arcs over a line,
lineage is a forest, coverage is a table. Drawing them all as one network would hide what
each says. See `docs/viz-plan.md`.

**Nothing is a fact the database stated.** The `relationships` table is empty; every edge
is derived by `bp/export.py`, and the coverage view prints `edge_kinds` so a reader can
see how each was built.

**No number is written into the page.** Counts come from the data at load, including the
ones in the prose. A viewer that hardcodes a figure from the snapshot it was built against
starts lying at the next export, and looks confident doing it.

**It leads with its own gaps**, because the failure mode of a graph viewer is looking
authoritative while silently dropping what it could not join. Every view says what it is
not showing: the entities with no edge, the references that resolve to nothing, the names
shared between entities, the forks with no date.

## One caveat worth repeating

`reports.channel` is free text with 90 distinct spellings. Only the channels the profile
declares carry a speed, and only a speed makes an arrival computable — 1,315 of 2,219 hops
match one. **A report on an undeclared channel is a real hop of unknown duration, not an
instant one and not an absent one.** Both viewers say so rather than rendering null as
zero-lag. Likewise an arrival *computed* from the space model is drawn differently from one
*stated* in the text: a checker that cannot tell those apart is guessing.

"""The story graph as an entity/relationship view, for anything outside `bp`.

A viewer, a notebook, a second tool — none of them should have to open the
SQLite file and rediscover what this module already knows. Two things in
particular are not obvious from the schema and cost a newcomer real time:

**The ``relationships`` table is empty, and that is not a bug.** Nothing in the
extraction pipeline writes it. Relationships in this graph are implicit,
scattered across five other tables: a replicant's ``parent_id``, the cast of an
event, who ``observed_by`` watching it, who shares a thread, and who sent word
to whom in ``reports``. :func:`derive_edges` makes them explicit as one typed
edge list so every consumer derives the same graph rather than each inventing
its own.

``reports`` deserves its own line, because it is the only table that says how a
character came to know something. ``beliefs`` records the outcome — Howard knew
— and ``reports`` records the route, the channel, and whether the arrival date
was *stated in the text* or *computed* from the profile's space model. Without
it a viewer can draw who knew what and nothing about how the news travelled.

**Entity references are not consistently entity ids.** The same field holds
``"bob-3-bill"`` (an id), ``"Garfield"`` (a display name) and ``"Enoki"`` (an
alias), sometimes in the same JSON array. :class:`Resolver` folds all three.

What this module deliberately does not export is the books. No ``scenes.text``,
no ``citations.quote``, no ``promises.paid_quote``. Everything here is either
structure or model-written paraphrase, which makes the export safe to hand to a
service that the corpus itself must never reach. :func:`verify_no_corpus_text`
is the assertion, not the comment.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Sequence

from .db import Graph
from .profile import SeriesProfile

#: How each derived edge kind is built, carried in the export so a consumer can
#: show the provenance of an edge it is drawing rather than assert it as fact.
EDGE_KINDS: dict[str, str] = {
    "forked_from": "entity -> its parent, from entities.parent_id — the replicant lineage",
    "declared": "from the relationships table, if anything ever writes it",
    "co_participant": "two entities named in one event's participants",
    "observed": "an entity in an event's observed_by -> an entity in its participants",
    "shares_thread": "two entities named in one thread's characters",
    "informed": "reports.sender -> reports.recipient — one hop of information, "
                "the route by which a belief reached someone",
}


def _jlist(raw: str | None) -> list[str]:
    """A JSON array column as a list of strings, tolerating anything else."""
    try:
        value = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _channel_view(profile: SeriesProfile | None, raw: str | None) -> dict[str, Any]:
    """Match a report's free-text channel to a channel the profile declares.

    ``reports.channel`` is whatever the extractor read off the page, so it is
    free text and there is a lot of it — the Bobiverse graph holds 90 distinct
    spellings across 2,219 reports, including "in-person", "in-person speech"
    and "in-person (VR)" as three separate values. A viewer that groups on the
    raw string draws a 90-item legend and learns nothing.

    The profile, though, declares the channels that actually have a *speed*
    (radio at c, SCUT instant), and speed is the only property that changes
    what a character could know. So the coarse field here is not a taxonomy of
    my own invention: it is "which declared channel is this, if any", and a
    ``None`` is the honest answer for the ~40% that match nothing — those
    reports have no computable arrival, and the viewer should say so rather
    than draw them as though it knew.
    """
    text = (raw or "").casefold()
    for ch in (profile.channels if profile else []):
        if ch.name.casefold() in text:
            return {"channel_declared": ch.name, "channel_instant": ch.instant}
    return {"channel_declared": None, "channel_instant": None}


@dataclass
class Resolver:
    """Turn any of the three reference spellings into one canonical entity id.

    The tiers matter more than they look. A name can belong to several entities
    at once — the corpus has six Guppys and three Alexanders — so the order
    below is what decides, and it is fixed so two runs agree:

    1. an exact entity id
    2. a unique exact match on ``name``
    3. a unique match on an alias
    4. several candidates: the most-cited one wins, and the reference is
       recorded in :attr:`ambiguous` with every candidate it could have been

    Tier 2 beating tier 3 is the one that earns its keep. ``Bob-1`` is the name
    of the first replicant and also an alias of a POV entity; resolving it by
    name is right, and refusing to resolve it at all — which is what dropping
    every ambiguous reference does — silently discards 7,630 mentions of the
    most-referenced character in the corpus. Losing that much of the graph
    quietly is worse than a tie-break that a consumer can see and override.
    """

    graph: Graph
    profile: SeriesProfile | None = None

    ids: set[str] = field(default_factory=set)
    by_name: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    by_alias: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    cited: Counter = field(default_factory=Counter)

    unresolved: Counter = field(default_factory=Counter)
    ambiguous: dict[str, list[str]] = field(default_factory=dict)
    ambiguous_hits: Counter = field(default_factory=Counter)

    def __post_init__(self) -> None:
        for row in self.graph.entities():
            self.ids.add(row["entity_id"])
            if row["name"] and row["name"].strip():
                self.by_name[row["name"].strip().casefold()].add(row["entity_id"])
            for alias in _jlist(row["aliases"]):
                if alias.strip():
                    self.by_alias[alias.strip().casefold()].add(row["entity_id"])
        # Weight of evidence, not authority — but it is at least deterministic,
        # and the entity the extractor cited most is the one a reader means.
        self.cited = Counter(
            r["record_id"] for r in self.graph.conn.execute(
                "SELECT record_id FROM citations WHERE record_kind='entity'")
        )

    def _tie_break(self, candidates: set[str], key: str) -> str:
        best = min(candidates, key=lambda i: (-self.cited[i], i))
        self.ambiguous.setdefault(key, sorted(candidates))
        self.ambiguous_hits[key] += 1
        return best

    def resolve(self, ref: str | None) -> str | None:
        """An id, a name or an alias -> a live entity id, or None if unknown."""
        if not isinstance(ref, str) or not ref.strip():
            return None
        ref = self.profile.canonical(ref) if self.profile else ref.strip()
        key = ref.casefold()
        if ref in self.ids:
            hit: str | None = ref
        elif len(self.by_name.get(key, ())) == 1:
            hit = next(iter(self.by_name[key]))
        elif len(self.by_alias.get(key, ())) == 1:
            hit = next(iter(self.by_alias[key]))
        elif self.by_name.get(key):
            hit = self._tie_break(self.by_name[key], key)
        elif self.by_alias.get(key):
            hit = self._tie_break(self.by_alias[key], key)
        else:
            hit = None
        # A merge rewrites the graph's own references but leaves a permanent
        # redirect behind, so an id from outside the graph may still be stale.
        hit = self.graph.resolve_entity_id(hit) if hit else None
        if hit is None or hit not in self.ids:
            self.unresolved[ref] += 1
            return None
        return hit

    def resolve_all(self, refs: Iterable[str]) -> list[str]:
        """Resolve a reference list, dropping what cannot be resolved."""
        return [r for r in (self.resolve(x) for x in refs) if r]


def derive_edges(entities: Sequence[dict], events: Sequence[dict],
                 threads: Sequence[dict],
                 declared: Sequence[dict] = (),
                 reports: Sequence[dict] = ()) -> list[dict[str, Any]]:
    """Every entity-to-entity relationship the graph implies, as one edge list.

    One edge per relationship, not per occurrence — a pair that recurs across
    forty events is one edge with ``count: 40`` and the first few ``via`` ids.
    That is the shape a viewer wants: the count is the edge's weight, and two
    characters who share forty scenes should not draw forty lines.

    Everything here arrives already resolved, which is deliberate. Resolution
    happens exactly once, in :func:`build_export`, so the resolver's tally
    counts each reference in the source data once rather than once per pair
    some later pass happens to consider.
    """
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add(a: str | None, b: str | None, kind: str,
            via: str = "", **detail: Any) -> None:
        if a is None or b is None or a == b:
            return
        # The symmetric kinds describe a pair, not a direction; ordering the
        # endpoints keeps A-B and B-A from both landing in the list.
        if kind in ("co_participant", "shares_thread") and a > b:
            a, b = b, a
        edge = edges.get((a, b, kind))
        if edge is None:
            edge = edges[(a, b, kind)] = {
                "source": a, "target": b, "kind": kind, "count": 0, "via": [], **detail}
        edge["count"] += 1
        if via and len(edge["via"]) < 8:
            edge["via"].append(via)

    for row in entities:
        if row.get("parent_entity"):
            add(row["entity_id"], row["parent_entity"], "forked_from",
                when=row.get("forked_on") or "", day=row.get("fork_day"))

    for row in declared:
        add(row.get("source_entity"), row.get("target_entity"), "declared",
            declared_kind=row.get("kind", ""), sentiment=row.get("sentiment", 0.0),
            since=row.get("since_text", ""), until=row.get("until_text", ""),
            detail=row.get("detail", ""))

    for ev in events:
        cast = sorted(set(ev["participants"]))
        for a, b in combinations(cast, 2):
            add(a, b, "co_participant", via=ev["event_id"])
        for watcher in sorted(set(ev["observed_by"])):
            for actor in cast:
                add(watcher, actor, "observed", via=ev["event_id"])

    for t in threads:
        cast = sorted(set(t["characters"]))
        for a, b in combinations(cast, 2):
            add(a, b, "shares_thread", via=t["thread_id"])

    # Who told whom. Directed, and never folded in with co_participant: sharing
    # a scene is not the same fact as one of them having sent word, and the
    # information path is the only edge kind that carries a delivery date.
    for rep in reports:
        add(rep.get("sender_entity"), rep.get("recipient_entity"), "informed",
            via=rep.get("event_id", ""))

    return list(edges.values())


def _rows(graph: Graph, sql: str) -> list[dict[str, Any]]:
    return [dict(r) for r in graph.conn.execute(sql)]


def _channel_quality(profile: SeriesProfile | None,
                     reports: Sequence[dict]) -> dict[str, Any]:
    """How much of the information path has a channel with a declared speed."""
    undeclared = Counter(r["channel"] for r in reports if not r["channel_declared"])
    return {
        "note": "reports.channel is free text. Only a channel the profile "
                "declares has a speed, and only a speed makes an arrival date "
                "computable. Reports on an undeclared channel are real hops "
                "with an unknown duration — not instant, not absent.",
        "declared": [c.name for c in (profile.channels if profile else [])],
        "matched_a_declared_channel": sum(1 for r in reports if r["channel_declared"]),
        "no_declared_channel": sum(undeclared.values()),
        "distinct_raw_channels": len({r["channel"] for r in reports}),
        "top_undeclared": undeclared.most_common(20),
        "arrival_computed": sum(1 for r in reports if r["computed"]),
        "arrival_stated": sum(1 for r in reports if not r["computed"]),
    }


def build_export(graph: Graph, profile: SeriesProfile | None = None) -> dict[str, dict]:
    """Build the two export documents: the graph view, and the per-record detail.

    They are split because they are read at different times. A relationship
    view needs entities and edges to draw anything at all; it needs an event's
    summary only once something is clicked. Splitting keeps the first load to
    about a third of the whole.
    """
    resolver = Resolver(graph, profile)

    entities = _rows(graph, "SELECT * FROM entities")
    for e in entities:
        e["aliases"] = _jlist(e["aliases"])
        e["parent_entity"] = resolver.resolve(e["parent_id"]) if e["parent_id"] else None

    threads = _rows(graph, "SELECT * FROM threads")
    for t in threads:
        t["characters"] = resolver.resolve_all(_jlist(t["characters"]))

    objects = _rows(graph, "SELECT * FROM objects")
    for o in objects:
        o["aliases"] = _jlist(o["aliases"])
        o["holder_entity"] = resolver.resolve(o["holder"])

    # Explicit columns, not SELECT *: scenes.text is the novel.
    scenes = _rows(
        graph,
        "SELECT scene_id, book_id, chapter, scene, chapter_title, pov, date_text,"
        "       day_lo, day_hi, place, cast_json, words, ord"
        "  FROM scenes ORDER BY ord",
    )
    for s in scenes:
        raw = _jlist(s.pop("cast_json"))
        s["cast"] = resolver.resolve_all(raw)
        s["cast_raw"] = raw

    events = _rows(graph, "SELECT * FROM events")
    for ev in events:
        ev["participants"] = resolver.resolve_all(_jlist(ev["participants"]))
        ev["observed_by"] = resolver.resolve_all(_jlist(ev["observed_by"]))
        ev["alternatives"] = _jlist(ev["alternatives"])

    declared = _rows(graph, "SELECT * FROM relationships")
    for r in declared:
        r["source_entity"] = resolver.resolve(r["source"])
        r["target_entity"] = resolver.resolve(r["target"])

    reports = _rows(graph, "SELECT * FROM reports")
    for rep in reports:
        rep["sender_entity"] = resolver.resolve(rep["sender"])
        rep["recipient_entity"] = resolver.resolve(rep["recipient"])
        rep.update(_channel_view(profile, rep["channel"]))

    edges = derive_edges(entities, events, threads, declared, reports)

    # Explicit columns, not SELECT *: promises.paid_quote is verbatim book text.
    # verify_no_corpus_text would catch a widened select, but it would catch it
    # by refusing to export at all, which is a bad way to learn this.
    promises = _rows(graph, "SELECT promise_id, summary, kind, planted_in, owed_by,"
                            "       status, paid_in, weight, claim_type, confidence"
                            "  FROM promises")
    for p in promises:
        p["planted_in"] = _jlist(p["planted_in"])
        p["owed_by"] = resolver.resolve_all(_jlist(p["owed_by"]))

    beliefs = _rows(graph, "SELECT * FROM beliefs")
    for b in beliefs:
        b["character_entity"] = resolver.resolve(b["character"])

    # cites_a/cites_b hold whole citation records, quote text included, so this
    # is the one table that cannot be handed over as stored. Reduced to the
    # scene ids, which is all a viewer can act on anyway. Empty today; it would
    # not have stayed empty, and the leak would have surfaced as a hard refusal
    # on the first run that opened a contradiction.
    contradictions = _rows(graph, "SELECT * FROM contradictions")
    for c in contradictions:
        for side in ("cites_a", "cites_b"):
            raw = json.loads(c[side] or "[]")
            c[side] = [x.get("scene", "") for x in raw if isinstance(x, dict)]
        c["resolved"] = bool(c["resolved"])

    degree: Counter = Counter()
    for e in edges:
        degree[e["source"]] += 1
        degree[e["target"]] += 1

    source = {
        "database": str(graph.path) if hasattr(graph, "path") else "",
        "generated_by": "bp export graph",
        "contains_no_book_text": "Scene prose, citation quotes and paid-promise "
                                 "quotes are all excluded. Summaries and "
                                 "descriptions are model-written paraphrase.",
        "relationships_table": "empty in the source database — the edges here are "
                               "derived; see edge_kinds for how each one is built",
        "files": {
            "graph.json": "entities, the derived edge list, books, scene metadata, "
                          "threads, objects",
            "detail.json": "events, the event causal chain, beliefs, reports, "
                           "promises, contradictions, citation index — fetched "
                           "on demand",
        },
    }

    counts = {
        "entities": len(entities),
        "edges": len(edges),
        "edges_by_kind": dict(Counter(e["kind"] for e in edges).most_common()),
        "entities_by_kind": dict(Counter(e["kind"] for e in entities).most_common()),
        "entities_by_status": dict(Counter(e["status"] for e in entities).most_common()),
        "isolated_entities": sum(1 for e in entities if not degree[e["entity_id"]]),
        "events": len(events),
        "threads": len(threads),
        "promises": len(promises),
        "objects": len(objects),
        "beliefs": len(beliefs),
        "reports": len(reports),
        "contradictions": len(contradictions),
        "scenes": len(scenes),
    }

    quality = {
        "note": "Entity references are stored inconsistently — sometimes an id, "
                "sometimes a display name, sometimes an alias. This export "
                "resolves all three, tie-breaks a name shared by several "
                "entities to the most-cited one, and drops what it cannot "
                "resolve at all. Both lists are here so a viewer can show the "
                "uncertainty instead of hiding it.",
        "unresolved_distinct": len(resolver.unresolved),
        "unresolved_mentions": sum(resolver.unresolved.values()),
        "top_unresolved": resolver.unresolved.most_common(60),
        "ambiguous_names": resolver.ambiguous,
        "ambiguous_mentions": sum(resolver.ambiguous_hits.values()),
        "ambiguous_mention_counts": resolver.ambiguous_hits.most_common(60),
        "channels": _channel_quality(profile, reports),
    }

    graph_doc = {
        "source": source,
        "edge_kinds": EDGE_KINDS,
        "counts": counts,
        "data_quality": quality,
        "books": _rows(graph, "SELECT * FROM books ORDER BY ord"),
        "entities": entities,
        "edges": edges,
        "threads": threads,
        "objects": objects,
        "scenes": scenes,
        "entity_merges": _rows(graph, "SELECT old_id, new_id FROM entity_merges"),
    }

    detail_doc = {
        "source": source,
        "counts": counts,
        "events": events,
        "event_edges": _rows(graph, "SELECT src, dst, kind FROM event_edges"),
        "promises": promises,
        "beliefs": beliefs,
        "reports": reports,
        "contradictions": contradictions,
        "citations": _rows(graph, "SELECT record_kind, record_id, scene_id FROM citations"),
    }
    return {"graph.json": graph_doc, "detail.json": detail_doc}


#: Shortest slice of prose distinctive enough that finding it in the export
#: means the export really is carrying the text, not colliding by chance.
_PROBE_CHARS = 40


@dataclass
class LeakCheck:
    """What a corpus-text check actually looked at, and what it found.

    ``probes`` is here because the first version of this check reported clean
    on a database whose scenes were all shorter than its minimum length: it
    built no probes, found nothing, and called that a pass. A guard that can
    succeed without looking at anything is the same bug this project keeps
    finding elsewhere — absence of evidence read as evidence of absence — and
    the count is what makes it visible.
    """

    probes: int = 0
    leaked: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.leaked


def verify_no_corpus_text(graph: Graph, blobs: Sequence[str], *,
                          samples: int = 12) -> LeakCheck:
    """Check that no serialised export carries verbatim book text.

    The export is built to exclude the corpus, but "built to" is a claim about
    intent. This is the claim about fact: it takes slices of real scene prose
    and real citation quotes straight out of the database and looks for them in
    what is about to be written.

    Cheap enough to run on every export, which is the point — a column added to
    ``scenes`` later and picked up by a ``SELECT *`` would otherwise ship the
    books quietly.
    """
    check = LeakCheck()
    needles: list[str] = []

    for row in graph.conn.execute(
            "SELECT text FROM scenes ORDER BY length(text) DESC LIMIT ?", (samples,)):
        text = row["text"] or ""
        if len(text) < _PROBE_CHARS:
            continue
        # From the middle, where prose is least likely to be a heading or a
        # stock opening line that could legitimately appear in a summary.
        start = max(0, len(text) // 2 - _PROBE_CHARS)
        needles.append(text[start:start + max(_PROBE_CHARS, 100)])

    for row in graph.conn.execute(
            "SELECT quote FROM citations ORDER BY length(quote) DESC LIMIT ?", (samples,)):
        quote = row["quote"] or ""
        if len(quote) >= _PROBE_CHARS:
            needles.append(quote[:100])

    check.probes = len(needles)
    check.leaked = [n for n in needles if any(n in blob for blob in blobs)]
    return check


def write_export(graph: Graph, out_dir: str | Path,
                 profile: SeriesProfile | None = None) -> tuple[dict[str, Path], LeakCheck]:
    """Build, verify and write the export. Raises if book text would ship.

    Nothing is written until the check has passed, so a refusal leaves no
    half-published file behind for someone to pick up and send on.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    docs = build_export(graph, profile)
    blobs = {name: json.dumps(doc, separators=(",", ":")) for name, doc in docs.items()}

    check = verify_no_corpus_text(graph, list(blobs.values()))
    if check.leaked:
        raise ValueError(
            f"export would carry {len(check.leaked)} slice(s) of verbatim book "
            f"text; first: {check.leaked[0][:60]!r}")

    written: dict[str, Path] = {}
    for name, blob in blobs.items():
        path = out / name
        path.write_text(blob)
        written[name] = path
    return written, check

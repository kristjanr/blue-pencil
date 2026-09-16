"""Entity resolution — deciding when two records are one thing.

The mechanical half of this lives in :func:`bp.extract._merge_entities` and only
collapses ids that differ in punctuation. That is deliberately timid, and it left
the real work undone: the same character arrives as `Bob Johansson` in one scene,
`Robert` in another and `Bob (original)` in a third, and no amount of string
comparison on *names* will connect them.

Two stages, and the split matters:

**Candidates** are found deterministically, by union-find over three signals —
equal names (plurals folded), one record's name appearing in another's aliases,
and two records sharing an alias. Notably this does NOT key on ``kind``: the
extractor calls one Jeeves a character and another an `other`, so requiring kind
to agree splits an entity in half on the least reliable field in the row.

**Verdicts** are then adjudicated per group. A group is not a yes/no question:
three Kevins can be two people, so the adjudicator returns a *partition*, and may
say it needs the scene text before it will commit.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_PUNCT = re.compile(r"[^a-z0-9]+")


def norm_name(s: str) -> str:
    """Fold case, punctuation, a trailing plural, and a parenthetical qualifier.

    `Bob (original)` and `bob-original` are the same string once the shape the
    model happened to pick is taken away.
    """
    s = re.sub(r"\((.*?)\)", r" \1 ", s or "")
    s = _PUNCT.sub(" ", s.lower()).strip()
    words = [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
             for w in s.split()]
    return " ".join(words)


class _Union:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, a: str) -> str:
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def join(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


@dataclass
class Candidate:
    """One set of records that may or may not be a single entity."""

    key: str
    members: list[dict] = field(default_factory=list)
    reasons: set[str] = field(default_factory=set)

    @property
    def names(self) -> list[str]:
        return sorted({m["name"] for m in self.members})


def candidates(graph, *, alias_shared_by_at_most: int = 3) -> list[Candidate]:
    """Every set of entity records that some signal says might be one entity.

    ``alias_shared_by_at_most`` is what keeps this from welding the whole cast
    into one record. In this series every replicant answers to "Bob", so an
    alias held by hundreds is a category, not an identity, and joining on it
    would put Barney and Bob Johansson in the same group. An alias only links
    records when few enough records claim it to mean something.
    """
    rows = [dict(r) for r in graph.conn.execute(
        "SELECT entity_id, name, kind, aliases, description, status, substrate, parent_id "
        "FROM entities")]
    by_id = {r["entity_id"]: r for r in rows}

    alias_map: dict[str, set[str]] = {}
    name_map: dict[str, set[str]] = {}
    for r in rows:
        name_map.setdefault(norm_name(r["name"]), set()).add(r["entity_id"])
        try:
            als = json.loads(r["aliases"] or "[]")
        except ValueError:
            als = []
        r["_aliases"] = [norm_name(a) for a in als if a]
        for a in r["_aliases"]:
            alias_map.setdefault(a, set()).add(r["entity_id"])

    u = _Union()
    reasons: dict[tuple[str, str], str] = {}
    for r in rows:
        u.find(r["entity_id"])

    # 1. same name
    for nm, ids in name_map.items():
        ids = sorted(ids)
        for other in ids[1:]:
            u.join(ids[0], other)
            reasons[(ids[0], other)] = "same name"
    # 2. a record's name is another's alias, same rarity rule
    for nm, ids in name_map.items():
        holders = alias_map.get(nm, ())
        if len(holders) > alias_shared_by_at_most or len(ids) > alias_shared_by_at_most:
            continue
        for holder in holders:
            for other in ids:
                if holder != other:
                    u.join(holder, other)
                    reasons[(holder, other)] = "name is an alias"
    # 3. two records share an alias — but only a discriminating one
    for a, ids in alias_map.items():
        if len(ids) > alias_shared_by_at_most:
            continue
        ids = sorted(ids)
        for other in ids[1:]:
            u.join(ids[0], other)
            reasons[(ids[0], other)] = "shared alias"

    groups: dict[str, Candidate] = {}
    for r in rows:
        root = u.find(r["entity_id"])
        groups.setdefault(root, Candidate(key=root)).members.append(r)
    for (a, b), why in reasons.items():
        root = u.find(a)
        if root in groups and by_id.get(b):
            groups[root].reasons.add(why)

    out = [c for c in groups.values() if len(c.members) > 1]
    out.sort(key=lambda c: -len(c.members))
    return out


# ------------------------------------------------------------------ adjudicate
from pydantic import BaseModel, Field  # noqa: E402

ADJUDICATE_SYSTEM = """You decide whether entity records extracted from a novel series
describe the SAME entity or different ones.

You are given a group of records that a deterministic pass flagged as possibly one
entity. The group is deliberately over-inclusive. Partition it.

What does NOT make two records different entities:
- A different name or nickname for the same being, including one used by only one
  character.
- A status that changed over time — alive in an early book, dead in a later one is
  one entity with a history, not two.
- The death of an avatar, game character, or body belonging to someone who survives
  it. A destroyed vessel or persona is not a destroyed person.
- A different substrate or body housing the same mind.
- Singular and plural forms of the same name.

What DOES make them different:
- Two beings who merely share a name or a role. Many individuals can hold the same
  job, run the same kind of AI, or operate the same class of equipment; a shared
  type is not a shared identity.
- A group or collective versus an individual member of it.
- A place, vessel or organisation versus a person associated with it.

Put every record in exactly one cluster. A group may split into several clusters,
and a cluster may hold a single record.

Flag a record as a DEFECT when it asserts something that reads as invented rather
than extracted, or when two records contradict each other on a fact that cannot
differ (not status over time — something that could never have been true).

Set needs_scene_text when the descriptions genuinely do not settle it and reading
the cited scenes would. Prefer this over guessing."""


class _Cluster(BaseModel):
    entity_ids: list[str] = Field(description="Records that are one entity")
    canonical_name: str = Field(default="", description="Best name for the merged record")
    reason: str = Field(default="", description="One sentence, citing what in the records decided it")
    confidence: float = Field(default=0.5, description="0 to 1")


class _Defect(BaseModel):
    entity_id: str
    problem: str


class Verdict(BaseModel):
    clusters: list[_Cluster] = []
    defects: list[_Defect] = []
    needs_scene_text: bool = False


def _render_group(c: Candidate, graph, *, snippet_chars: int = 240) -> str:
    lines = []
    for m in sorted(c.members, key=lambda r: r["entity_id"]):
        cites = [r[0] for r in graph.conn.execute(
            "SELECT scene_id FROM citations WHERE record_kind='entity' AND record_id=? LIMIT 4",
            (m["entity_id"],))]
        try:
            als = json.loads(m["aliases"] or "[]")
        except ValueError:
            als = []
        desc = (m["description"] or "").strip()[:snippet_chars]
        lines.append(
            f"- id: {m['entity_id']}\n"
            f"  name: {m['name']}\n"
            f"  kind: {m['kind']}   status: {m['status'] or 'unknown'}"
            + (f"   substrate: {m['substrate']}" if m["substrate"] else "")
            + (f"\n  aliases: {', '.join(als[:6])}" if als else "")
            + (f"\n  parent_id: {m['parent_id']}" if m["parent_id"] else "")
            + (f"\n  description: {desc}" if desc else "")
            + (f"\n  seen in: {', '.join(cites)}" if cites else "")
        )
    return "Records in this group:\n\n" + "\n".join(lines)


def adjudicate(client, model: str, c: Candidate, graph, *, usage=None) -> Verdict:
    """Ask a model to partition one candidate group."""
    from .llm import structured

    return structured(client, model, Verdict, system=ADJUDICATE_SYSTEM,
                      prompt=_render_group(c, graph), max_tokens=4_000,
                      usage=usage, stage="resolve:adjudicate")


# ----------------------------------------------------------------- applying
@dataclass
class ApplyReport:
    """What applying a verdicts file did, or would do."""

    groups: int = 0
    absorbed: int = 0
    skipped: list[str] = field(default_factory=list)
    planned: list[tuple[str, list[str]]] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"{self.groups} merge group(s) · {self.absorbed} record(s) absorbed"]
        for keeper, losers in self.planned:
            lines.append(f"  {keeper} <- {', '.join(losers)}")
        for note in self.skipped:
            lines.append(f"  skipped: {note}")
        return "\n".join(lines)


def load_verdicts(path) -> list[tuple[str, list[str]]]:
    """Read a verdicts file into (keeper, losers) pairs.

    Only ``merge`` and ``partition`` entries move anything. ``split`` says the
    records are different people and ``hold`` says a human has not decided —
    both are deliberately no-ops here, because the standing rule is that a
    merge needs positive evidence and silence is not evidence.

    A ``partition`` entry carries ``leave_alone`` beside its ``absorb`` list;
    those ids are simply not absorbed, so they need no handling beyond being
    left out.
    """
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: list[tuple[str, list[str]]] = []
    for section in ("merge", "partition"):
        for entry in data.get(section) or []:
            keeper = (entry or {}).get("keep")
            losers = [x for x in ((entry or {}).get("absorb") or []) if x]
            if keeper and losers:
                out.append((keeper, losers))
    return out


def apply_verdicts(graph, path, *, apply: bool = False) -> ApplyReport:
    """Apply a verdicts file's merges to the graph.

    Dry by default: a merge deletes records, and the standing rule is that a
    wrong merge is the expensive mistake — it destroys a distinction with no
    trace left to audit. Seeing the plan before it runs is cheap.
    """
    from .extract import merge_group

    report = ApplyReport()
    for keeper, losers in load_verdicts(path):
        live = [
            lid for lid in losers
            if graph.conn.execute(
                "SELECT 1 FROM entities WHERE entity_id=?", (lid,)).fetchone()
        ]
        if not live:
            report.skipped.append(f"{keeper}: every record already absorbed")
            continue
        if graph.conn.execute(
                "SELECT 1 FROM entities WHERE entity_id=?", (keeper,)).fetchone() is None:
            report.skipped.append(f"{keeper}: keeper is not an entity in this graph")
            continue
        report.groups += 1
        report.planned.append((keeper, live))
        if apply:
            report.absorbed += merge_group(graph, keeper, live)
        else:
            report.absorbed += len(live)
    if apply:
        graph.conn.commit()
    return report

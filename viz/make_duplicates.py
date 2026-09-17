#!/usr/bin/env python3
"""Duplicate-candidate groups as JSON, for the atlas's Duplicates view.

One character recorded as several entities is the defect that gates step 1 of the
evaluation ladder: `knowledge.last_placement()` matches scene cast against a single
canonical name, so a split character is invisible in half his scenes and the geography
checker reports a stale position *with full confidence*. A false alarm with a straight
face is worse than noise.

This calls :func:`bp.resolve.candidates` — the same function `bp resolve` uses — against
the entities in an export, rather than reimplementing the rule. The alternative was a
second copy of the matching logic in JavaScript, drifting from the first.

**A group is a question, not a bug.** `candidates()` is deliberately over-inclusive so an
adjudicator can partition it: seven Survey Drone records really are several drones, and
`Guppy` is one GUPPI instance per replicant, which is a distinction the schema cannot
currently express at all. The view says so; this file just supplies the counts.

Usage:  python3 viz/make_duplicates.py viz/graph.json > viz/duplicates.json
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bp.resolve import candidates  # noqa: E402

VERDICTS = Path(__file__).resolve().parent.parent / "eval" / "entity_duplicate_verdicts.yaml"
# Only what candidates() reads. Kept narrow on purpose: a wider table would invite
# this script to start deriving things the export should be emitting instead.
COLUMNS = ("entity_id", "name", "kind", "aliases", "description", "status",
           "substrate", "parent_id")


def _memory_graph(entities: list[dict]):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(f"CREATE TABLE entities ({', '.join(c + ' TEXT' for c in COLUMNS)})")
    conn.executemany(
        f"INSERT INTO entities VALUES ({','.join('?' * len(COLUMNS))})",
        [(e["entity_id"], e["name"], e.get("kind", ""),
          json.dumps(e.get("aliases") or []), e.get("description", ""),
          e.get("status", ""), e.get("substrate", ""), e.get("parent_id") or "")
         for e in entities])
    return type("MemGraph", (), {"conn": conn})()


def _verdicts() -> dict[str, dict]:
    """entity_id -> the recorded human/model verdict, if this group has one."""
    if not VERDICTS.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    doc = yaml.safe_load(VERDICTS.read_text()) or {}
    out: dict[str, dict] = {}
    for section in ("merge", "partition", "split", "hold"):
        for g in doc.get(section) or []:
            ids = [i for i in ([g.get("keep")] + (g.get("absorb") or [])
                               + (g.get("members") or [])) if i]
            for grp in (g.get("groups") or []):
                if isinstance(grp, list):
                    ids += grp
            note = g.get("why") or g.get("question") or ""
            for i in ids:
                out[i] = {"verdict": section, "note": " ".join(str(note).split()),
                          "keep": g.get("keep") or "", "group_id": str(g.get("id", ""))}
    return out


def build(graph: dict) -> dict:
    entities = graph["entities"]
    by_id = {e["entity_id"]: e for e in entities}
    groups = candidates(_memory_graph(entities))
    verdicts = _verdicts()
    merged_away = {m["old_id"]: m["new_id"] for m in graph.get("entity_merges", [])}

    # How often each record is named in a scene cast — a split character who appears
    # everywhere costs far more than one who appears twice, so this is the ranking.
    cast_hits: Counter = Counter()
    for s in graph.get("scenes", []):
        for cid in (s.get("cast") or []):
            cast_hits[cid] += 1
    total_cast = sum(cast_hits.values())

    degree: Counter = Counter()
    for e in graph.get("edges", []):
        degree[e["source"]] += 1
        degree[e["target"]] += 1

    out = []
    for c in groups:
        members = []
        for m in c.members:
            e = by_id.get(m["entity_id"], {})
            members.append({
                "entity_id": m["entity_id"], "name": m["name"], "kind": m.get("kind") or "",
                "status": e.get("status", ""), "description": e.get("description", ""),
                "aliases": e.get("aliases") or [],
                "claim_type": e.get("claim_type", ""), "confidence": e.get("confidence"),
                "cast_entries": cast_hits.get(m["entity_id"], 0),
                "edges": degree.get(m["entity_id"], 0),
                "verdict": verdicts.get(m["entity_id"], {}).get("verdict", ""),
            })
        members.sort(key=lambda m: (-m["cast_entries"], -m["edges"], m["name"]))
        vs = [verdicts[m["entity_id"]] for m in members if m["entity_id"] in verdicts]
        kinds = Counter(m["kind"] for m in members)
        out.append({
            "key": c.key,
            "members": members,
            "reasons": sorted(c.reasons),
            "dominant_kind": kinds.most_common(1)[0][0] if kinds else "other",
            "kinds": dict(kinds),
            "cast_entries": sum(m["cast_entries"] for m in members),
            "verdict": vs[0]["verdict"] if vs else "",
            "verdict_note": vs[0]["note"] if vs else "",
            "verdict_group": vs[0]["group_id"] if vs else "",
        })
    out.sort(key=lambda g: (-g["cast_entries"], -len(g["members"])))

    affected = sum(g["cast_entries"] for g in out)
    return {
        "source": {
            "computed_by": "bp.resolve.candidates, via viz/make_duplicates.py",
            "rule": "records are linked when they share a name, when one record's name is "
                    "another's alias, or when they share an alias held by at most 3 records "
                    "(an alias any more records claim is a category like \"Bob\", not an identity)",
            "caveat": "The candidate set is deliberately over-inclusive — it is the input to "
                      "adjudication, not a list of defects. Some groups are genuinely several "
                      "things that share a name.",
            "verdicts_from": str(VERDICTS.relative_to(VERDICTS.parent.parent)),
        },
        "counts": {
            "groups": len(out),
            "records": sum(len(g["members"]) for g in out),
            "entities": len(entities),
            "cast_entries_affected": affected,
            "cast_entries_total": total_cast,
            "groups_by_kind": dict(Counter(g["dominant_kind"] for g in out)),
            "groups_by_verdict": dict(Counter(g["verdict"] or "none" for g in out)),
            "merges_applied": len(merged_away),
        },
        "groups": out,
    }


if __name__ == "__main__":
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "viz/graph.json")
    json.dump(build(json.loads(src.read_text())), sys.stdout, ensure_ascii=False)

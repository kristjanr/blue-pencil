"""Stage 2 — the story graph.

The expensive one-time step, and the thing that makes everything after it cheap.

Four passes, run per scene through the Batch API because latency does not matter
here and half price does. Each pass emits schema-valid records carrying the
scene ID and the quote that supports them; :mod:`bp.db` rejects any record that
arrives without one.

Extraction is deliberately over-inclusive on the first pass and pruned by a
second, cheaper pass that merges duplicates, scores confidence, and records
contradictions between scenes as **open records rather than resolving them**.
That last part is the difference between a story bible and a wiki. In a series
built on unreliable narrators, the contradiction is often the story, and a
pipeline that silently picks a winner has thrown away the evidence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from pydantic import BaseModel, ValidationError

from .db import Graph
from .errors import UncitedClaim
from .llm import (
    BATCH_DISCOUNT, Usage, _cacheable, poll_batch, rate_for, schema_tokens, structured, submit_batch,
)
from .models import (
    Citation, Contradiction, Entity, Event, ObjectRecord, Promise, TechniqueSpec, Thread,
)
from .policy import RunPolicy
from .profile import SeriesProfile

SYSTEM = """You are an extraction agent building a story bible from a novel series.

Rules that matter more than completeness:
- Every record must carry a citation: the scene ID you were given, and a short verbatim quote.
- Mark each record explicit (the text says so), inferred (you worked it out), or disputed
  (the text supports more than one reading). Give a confidence.
- When two readings are defensible, record BOTH in `alternatives`. Never quietly pick one.
- Do not compute dates, distances or arrival times. Record what the text states; the engine
  does the arithmetic from the series profile.
- Prefer too many records over too few. A later pass merges duplicates."""


class _Entities(BaseModel):
    items: list[Entity]


class _Events(BaseModel):
    items: list[Event]


class _Ledger(BaseModel):
    objects: list[ObjectRecord] = []
    promises: list[Promise] = []
    threads: list[Thread] = []


class _Mechanism(BaseModel):
    """The mechanism half of a technique spec — the half a model can see and a
    ruler cannot."""

    narrative_distance: str = ""
    exposition_mode: str = ""
    emotion_mode: str = ""
    devices: list[str] = []
    tics: list[str] = []
    drifted_from: str = ""
    notes: str = ""
    exemplars: list[dict[str, str]] = []   # {scene, situation, techniques, excerpt}


PASSES = ("entities", "events", "ledger", "technique")


@dataclass
class ExtractReport:
    scenes: int = 0
    entities: int = 0
    events: int = 0
    reports: int = 0
    beliefs: int = 0
    objects: int = 0
    promises: int = 0
    threads: int = 0
    contradictions: int = 0
    rejected_uncited: int = 0
    citations_repinned: int = 0
    fields_dropped: int = 0
    records_salvaged: int = 0
    errors: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    passes_done: list[str] = field(default_factory=list)
    stopped: str = ""

    def render(self) -> str:
        lines = [
            f"{self.scenes} scenes extracted",
            f"entities {self.entities} · events {self.events} "
            f"(reports {self.reports}, beliefs {self.beliefs})",
            f"objects {self.objects} · promises {self.promises} · threads {self.threads}",
            f"contradictions kept open: {self.contradictions}",
            f"records rejected for having no citation: {self.rejected_uncited}",
            f"citations re-pinned to the scene they came from: {self.citations_repinned}",
            f"salvaged: {self.fields_dropped} unknown fields dropped, "
            f"{self.records_salvaged} records with an out-of-vocabulary value",
            self.usage.render(),
        ]
        if self.stopped:
            lines.append(f"STOPPED EARLY: {self.stopped}")
        lines += [f"error: {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def _scene_prompt(scene, profile: SeriesProfile, pass_name: str) -> str:
    head = (
        f"SERIES: {profile.name}\n"
        f"SCENE ID: {scene.scene_id} (book {scene.book_id}, chapter {scene.chapter}, scene {scene.scene})\n"
        f"POV as tagged at ingest: {scene.pov or 'unknown'}\n"
        f"Date as tagged at ingest: {scene.date_text or 'unknown'}\n"
        f"Place as tagged at ingest: {scene.place or 'unknown'}\n"
        f"Channels information can travel on in this series: "
        f"{', '.join(c.name for c in profile.channels) or 'none declared'}\n\n"
    )
    task = {
        "entities": (
            "Extract every character, faction, place, ship and organisation that appears. "
            "Record physical state and, if this series tracks it, what substrate or body they "
            "are running on — 'where is this character' and 'what are they running on' are "
            "different questions. If a character is a copy of another, set parent_id and forked_on."
        ),
        "events": (
            "Extract the events in this scene. For each: who observed it, who reported it to "
            "whom and over which channel, and what each named character believes about it — "
            "including false beliefs. Leave `arrives` empty; the engine computes arrival times. "
            "Record consequences: what this event cost, which options it closed, which "
            "obligations it created."
        ),
        "ledger": (
            "Extract three ledgers. Objects: named significant items, where they are and who "
            "holds them. Promises: prophecies, vows, foreshadowing, dangling setups — anything "
            "the text has promised to pay off. Threads: plotlines with their current state."
        ),
        "technique": (
            "Describe how this POV is written. Narrative distance; how exposition is delivered; "
            "how emotion is disclosed; recurring devices and verbal tics. Then pick 1–3 passages "
            "as exemplars, each tagged with the SITUATION it occurs in and the TECHNIQUES it "
            "demonstrates, so a later stage can retrieve 'this POV, in this kind of moment'."
        ),
    }[pass_name]
    return f"{head}TASK: {task}\n\n--- SCENE TEXT ---\n{scene.text}"


def _schema_for(pass_name: str):
    return {"entities": _Entities, "events": _Events, "ledger": _Ledger, "technique": _Mechanism}[pass_name]


# --------------------------------------------------------------------- writing
def _write_records(graph: Graph, scene_id: str, pass_name: str, payload: Any, report: ExtractReport) -> None:
    """Commit a pass's output, dropping anything that cannot cite itself."""

    def cited(rec) -> bool:
        # Each request carries exactly one scene, so every citation it returns
        # refers to that scene and nothing else. The model does not always echo
        # the ID verbatim, though — it normalises `book 5.35.1` to `book5.35.1`
        # or `B5.35.1` — and a citation pointing at an ID no scene has is a
        # dangling reference the audit cannot follow. Pin it to the scene we sent.
        for c in rec.citations:
            if c.scene != scene_id:
                c.scene = scene_id
                report.citations_repinned += 1
        if not rec.citations:
            # Extraction was told to cite. If it didn't, we know the scene, so
            # backfill the scene ID with an empty quote rather than lose the
            # record — but mark it inferred, because an uncited claim is exactly
            # the kind the audit needs to look at.
            rec.citations = [Citation(scene=scene_id, quote="")]
            rec.claim_type = "inferred"
            rec.confidence = min(rec.confidence, 0.5)
            report.rejected_uncited += 1
        return True

    if pass_name == "entities":
        for e in payload.items:
            cited(e)
            graph.write_entity(e)
            report.entities += 1
    elif pass_name == "events":
        for ev in payload.items:
            cited(ev)
            graph.write_event(ev)
            report.events += 1
            report.reports += len(ev.reports)
            report.beliefs += len(ev.beliefs)
    elif pass_name == "ledger":
        for o in payload.objects:
            cited(o)
            graph.write_object(o)
            report.objects += 1
        for p in payload.promises:
            cited(p)
            graph.write_promise(p)
            report.promises += 1
        for t in payload.threads:
            cited(t)
            graph.write_thread(t)
            report.threads += 1
    elif pass_name == "technique":
        scene = graph.scene(scene_id)
        pov = scene.pov if scene else ""
        if pov:
            spec = graph.style(pov) or TechniqueSpec(pov=pov)
            spec.narrative_distance = payload.narrative_distance or spec.narrative_distance
            spec.exposition_mode = payload.exposition_mode or spec.exposition_mode
            spec.emotion_mode = payload.emotion_mode or spec.emotion_mode
            spec.devices = sorted(set(spec.devices) | set(payload.devices))[:20]
            spec.tics = sorted(set(spec.tics) | set(payload.tics))[:20]
            spec.drifted_from = payload.drifted_from or spec.drifted_from
            spec.notes = payload.notes or spec.notes
            graph.write_style(spec)
        for ex in payload.exemplars:
            graph.conn.execute(
                "INSERT INTO exemplars(scene_id,pov,situation,techniques,excerpt) VALUES(?,?,?,?,?)",
                (scene_id, pov, ex.get("situation", ""),
                 json.dumps([t.strip() for t in str(ex.get("techniques", "")).split(",") if t.strip()]),
                 ex.get("excerpt", "")),
            )


# --------------------------------------------------------------------- budget
# Output tokens can only be guessed before a pass runs. Measured on the entities
# pass at ~2,200 per scene; round up, because an estimate that guards a spend cap
# should err towards refusing to spend.
ASSUMED_OUTPUT_TOKENS = 2_500


def _estimate_pass_usd(client, model, profile, pass_name, schema, scene_rows, *, batch: bool) -> float:
    """What the upcoming pass should cost, priced before a token of it is spent.

    Input is counted for real (``count_tokens`` is free) on a sample of scenes and
    scaled; output is the constant above. Caching is ignored, so the number is a
    ceiling — which is what a cap wants.
    """
    if not scene_rows:
        return 0.0
    tool = {"name": "emit", "description": f"Emit {schema.__name__}.",
            "input_schema": schema.model_json_schema()}
    sample = scene_rows[:: max(1, len(scene_rows) // 8)][:8]
    counted = []
    for scene in sample:
        try:
            n = client.messages.count_tokens(
                model=model, system=SYSTEM, tools=[tool],
                messages=[{"role": "user", "content": _scene_prompt(scene, profile, pass_name)}],
            ).input_tokens
        except Exception:
            continue
        counted.append(int(n))
    if not counted:
        # No usable count (offline, or a stub client) — fall back to a coarse
        # chars/4 estimate rather than silently pricing the pass at zero.
        counted = [len(_scene_prompt(s, profile, pass_name)) // 4 + schema_tokens(tool["input_schema"])
                   for s in sample]

    per_scene_in = sum(counted) / len(counted)
    rate_in, rate_out = rate_for(model)
    usd = len(scene_rows) * (per_scene_in * rate_in + ASSUMED_OUTPUT_TOKENS * rate_out) / 1_000_000
    return usd * (BATCH_DISCOUNT if batch else 1.0)


# ------------------------------------------------------------------- interface
def extract(
    graph: Graph,
    profile: SeriesProfile,
    policy: RunPolicy,
    client,
    *,
    passes: Iterable[str] = PASSES,
    scenes: list | None = None,
    use_batch: bool = True,
    max_usd: float | None = None,
    progress: Callable[[str], None] = lambda _: None,
) -> ExtractReport:
    report = ExtractReport()
    scene_rows = scenes if scenes is not None else graph.scenes()
    report.scenes = len(scene_rows)
    model = policy.model_for("extract")

    for pass_name in passes:
        schema = _schema_for(pass_name)
        progress(f"pass {pass_name}: {len(scene_rows)} scenes")
        if max_usd is not None:
            est = _estimate_pass_usd(client, model, profile, pass_name, schema,
                                     scene_rows, batch=use_batch)
            spent = report.usage.usd
            progress(f"  spent ${spent:,.2f} · this pass ≈ ${est:,.2f} · cap ${max_usd:,.2f}")
            if spent + est > max_usd:
                report.stopped = (
                    f"cap ${max_usd:,.2f} would be exceeded at pass {pass_name} "
                    f"(${spent:,.2f} spent + ${est:,.2f} estimated). "
                    f"Passes completed: {', '.join(report.passes_done) or 'none'}."
                )
                progress(f"  {report.stopped}")
                break
        if use_batch:
            _run_batch(graph, profile, client, model, pass_name, schema, scene_rows, report, progress)
        else:
            for scene in scene_rows:
                try:
                    payload = structured(
                        client, model, schema, system=SYSTEM,
                        prompt=_scene_prompt(scene, profile, pass_name),
                        max_tokens=12_000, usage=report.usage, stage=f"extract:{pass_name}",
                    )
                    _write_records(graph, scene.scene_id, pass_name, payload, report)
                except (UncitedClaim, Exception) as exc:
                    report.errors.append(f"{scene.scene_id}/{pass_name}: {type(exc).__name__}: {exc}")
        graph.commit()
        report.passes_done.append(pass_name)
        if max_usd is not None and report.usage.usd > max_usd:
            report.stopped = (
                f"cap ${max_usd:,.2f} passed after {pass_name} (${report.usage.usd:,.2f} spent). "
                f"Passes completed: {', '.join(report.passes_done)}."
            )
            progress(f"  {report.stopped}")
            break

    prune(graph, profile, report)
    graph.commit()
    return report


def _walk(data, loc):
    """The container holding ``loc[-1]``, or None if the path does not exist."""
    node = data
    for key in loc[:-1]:
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            return None
    return node


def _salvage(schema, data, report: "ExtractReport", where: str):
    """Validate a pass's payload, losing the bad record instead of the whole scene.

    ``extra="forbid"`` is deliberate — it is what stops the extraction schema and
    the table schema drifting apart unnoticed — but combined with whole-payload
    validation it means one invented field costs a scene every record it had.
    So keep the strictness and narrow the blast radius: drop the unknown field,
    drop the record whose enum is outside the vocabulary, and validate again.

    An enum is never guessed. A belief that arrives as ``believes`` could mean
    ``knows`` or ``believes_false``, and those are opposites to the epistemic
    checker, so the record goes rather than the polarity being invented.
    """
    for _ in range(60):
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            progressed = False
            for err in exc.errors():
                loc, kind = list(err["loc"]), err["type"]
                if not loc:
                    continue
                parent = _walk(data, loc)
                if parent is None:
                    continue
                if kind == "extra_forbidden":
                    try:
                        del parent[loc[-1]]
                    except (KeyError, IndexError, TypeError):
                        continue
                    report.fields_dropped += 1
                    progressed = True
                elif kind in ("literal_error", "enum") or kind.startswith("enum"):
                    # Remove the record that carries the bad value, not the field:
                    # a belief with no state is not a belief.
                    for depth in range(len(loc) - 1, 0, -1):
                        holder, key = _walk(data, loc[:depth]), loc[depth - 1]
                        if isinstance(holder, list) and isinstance(key, int):
                            del holder[key]
                            report.records_salvaged += 1
                            progressed = True
                            break
                    else:
                        continue
                if progressed:
                    break
            if not progressed:
                raise
    report.errors.append(f"{where}: salvage gave up after 60 repairs")
    raise ValidationError.from_exception_data(schema.__name__, [])


def _custom_id(pass_name: str, scene_id: str) -> str:
    """A batch ``custom_id`` the API will accept: ``[a-zA-Z0-9_-]`` only, ≤64 chars.

    Scene IDs carry a space (``book 1.01.1``) because the profile addresses books
    by that exact string — ``available_from: "book 2"`` has to resolve — so the
    shape that suits the graph is not the shape the wire allows. Squash anything
    outside the allowed set rather than changing the scene ID.
    """
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{pass_name}--{scene_id}")
    return slug[:64]


def _run_batch(graph, profile, client, model, pass_name, schema, scene_rows, report, progress) -> None:
    raw_schema = schema.model_json_schema()
    tool = {
        "name": "emit",
        "description": f"Emit {schema.__name__}.",
        "input_schema": raw_schema,
    }
    # Tools render before system, so a breakpoint on the system block caches the
    # tool schema with it — and the schema is the bulk of the shared prefix here
    # (~1,200 tokens for the events pass against ~175 of system text). Whether
    # that clears the model's minimum depends on the pass: events and ledger do,
    # entities and technique do not, and marking one that cannot cache would
    # spend a breakpoint slot for nothing. Let the helper decide per pass.
    system_blocks = _cacheable([SYSTEM], model=model, prefix_tokens=schema_tokens(raw_schema))
    requests = [
        {
            "custom_id": _custom_id(pass_name, scene.scene_id),
            "params": {
                "model": model,
                "max_tokens": 12_000,
                "system": system_blocks,
                "tools": [tool],
                "tool_choice": {"type": "tool", "name": "emit"},
                "messages": [{"role": "user", "content": _scene_prompt(scene, profile, pass_name)}],
            },
        }
        for scene in scene_rows
    ]
    by_id = {r["custom_id"]: s for r, s in zip(requests, scene_rows)}
    if len(by_id) != len(requests):
        # Two scene IDs squashed to the same custom_id; results would be filed
        # against the wrong scene. Refuse rather than corrupt the graph.
        raise ValueError(f"{len(requests) - len(by_id)} scene IDs collide as batch custom_ids")
    batch_id = submit_batch(client, requests)
    progress(f"  batch {batch_id} submitted; polling")
    for result in poll_batch(client, batch_id):
        scene = by_id.get(result.custom_id)
        if scene is None:
            continue
        if result.result.type != "succeeded":
            report.errors.append(f"{result.custom_id}: {result.result.type}")
            continue
        msg = result.result.message
        report.usage.add(model, msg.usage, stage=f"extract:{pass_name}", batch=True)
        try:
            block = next(b for b in msg.content if getattr(b, "type", "") == "tool_use")
            payload = _salvage(schema, block.input, report, f"{scene.scene_id}/{pass_name}")
            _write_records(graph, scene.scene_id, pass_name, payload, report)
        except Exception as exc:
            report.errors.append(f"{scene.scene_id}/{pass_name}: {type(exc).__name__}: {exc}")


# ----------------------------------------------------------------------- prune
def prune(graph: Graph, profile: SeriesProfile, report: ExtractReport) -> None:
    """Second pass: merge duplicates and open a contradiction record for conflicts.

    This runs deterministically. Duplicate detection is exact-ish (normalised
    summary within the same place and date), and conflict detection is over
    *facts the graph itself can compare* — a character recorded both alive and
    dead, an object in two places on the same day. Those are the conflicts worth
    surfacing, and none of them needs a model.
    """
    # Duplicate events: same normalised summary, same place, overlapping date.
    seen: dict[tuple, str] = {}
    for row in graph.events():
        key = (
            " ".join(sorted(row["summary"].lower().split()))[:120],
            (row["where_place"] or "").casefold(),
            round(row["day_lo"] / 30) if row["day_lo"] is not None else None,
        )
        if key in seen:
            keeper = seen[key]
            graph.conn.execute(
                "UPDATE reports SET event_id=? WHERE event_id=?", (keeper, row["event_id"]))
            graph.conn.execute(
                "UPDATE beliefs SET event_id=? WHERE event_id=?", (keeper, row["event_id"]))
            graph.conn.execute(
                "UPDATE citations SET record_id=? WHERE record_kind='event' AND record_id=?",
                (keeper, row["event_id"]))
            graph.conn.execute("DELETE FROM events WHERE event_id=?", (row["event_id"],))
        else:
            seen[key] = row["event_id"]

    # Objects recorded in two places on the same day.
    rows = graph.conn.execute(
        "SELECT object_id, name, location, as_of_day FROM objects WHERE location != '' ORDER BY name, as_of_day"
    ).fetchall()
    by_name: dict[str, list] = {}
    for r in rows:
        by_name.setdefault(r["name"].casefold(), []).append(r)
    for name, entries in by_name.items():
        for a, b in zip(entries, entries[1:]):
            if a["location"].casefold() == b["location"].casefold():
                continue
            if a["as_of_day"] is None or b["as_of_day"] is None or abs(a["as_of_day"] - b["as_of_day"]) > 1:
                continue
            cid = f"C-obj-{a['object_id']}-{b['object_id']}"
            graph.write_contradiction(Contradiction(
                contradiction_id=cid, subject=f"location of {a['name']}",
                reading_a=f"{a['location']} (record {a['object_id']})",
                reading_b=f"{b['location']} (record {b['object_id']})",
                citations_a=graph.citations_for("object", a["object_id"]),
                citations_b=graph.citations_for("object", b["object_id"]),
                note="an object cannot be in two places on the same day",
            ))
            report.contradictions += 1

    # Belief records that disagree about the same character and event.
    for row in graph.conn.execute(
        """SELECT event_id, character, GROUP_CONCAT(DISTINCT state) AS states
           FROM beliefs GROUP BY event_id, character HAVING COUNT(DISTINCT state) > 1"""
    ):
        states = row["states"].split(",")
        if {"knows", "unaware"} <= set(states) or "believes_false" in states:
            cid = f"C-bel-{row['event_id']}-{row['character']}"
            graph.write_contradiction(Contradiction(
                contradiction_id=cid,
                subject=f"{row['character']}'s belief about {row['event_id']}",
                reading_a=states[0], reading_b=", ".join(states[1:]),
                citations_a=graph.citations_for("event", row["event_id"]),
                note=("kept open: in a series with unreliable narrators, a character recorded as "
                      "both knowing and unaware is often the point rather than an error"),
            ))
            report.contradictions += 1

    # A character recorded dead who later participates in an event.
    for ent in graph.conn.execute("SELECT entity_id, name FROM entities WHERE status='dead'"):
        later = graph.conn.execute(
            """SELECT event_id, summary FROM events
               WHERE participants LIKE ? ORDER BY day_lo DESC LIMIT 1""",
            (f'%"{ent["entity_id"]}"%',),
        ).fetchone()
        if later is None:
            continue
        cid = f"C-dead-{ent['entity_id']}"
        graph.write_contradiction(Contradiction(
            contradiction_id=cid, subject=f"{ent['name']} is recorded dead but acts later",
            reading_a="dead (entity record)",
            reading_b=f"participates in {later['event_id']}: {later['summary']}",
            citations_a=graph.citations_for("entity", ent["entity_id"]),
            note="if the series allows restoration, set entities.revivable in the profile",
        ))
        report.contradictions += 1


def audit_sample(graph: Graph, n: int = 50, *, seed: int = 0) -> list[dict]:
    """A random sample of claims with their citations, for the Phase 1 spot audit.

    The exit test for the story graph is a human checking fifty claims against
    the text. This produces that worksheet — the point being that the number is
    measured, not asserted.
    """
    import random

    rng = random.Random(seed)
    rows = graph.conn.execute(
        """SELECT record_kind, record_id, scene_id, quote FROM citations
           ORDER BY record_kind, record_id"""
    ).fetchall()
    if not rows:
        return []
    picks = rng.sample(rows, min(n, len(rows)))
    out = []
    for r in picks:
        table, id_col, label_col = {
            "event": ("events", "event_id", "summary"),
            "entity": ("entities", "entity_id", "name"),
            "object": ("objects", "object_id", "name"),
            "promise": ("promises", "promise_id", "summary"),
            "thread": ("threads", "thread_id", "name"),
        }.get(r["record_kind"], (None, None, None))
        claim = ""
        if table:
            row = graph.conn.execute(
                f"SELECT {label_col} AS label FROM {table} WHERE {id_col}=?", (r["record_id"],)
            ).fetchone()
            claim = row["label"] if row else "(record deleted by prune)"
        out.append({
            "kind": r["record_kind"], "id": r["record_id"], "claim": claim,
            "scene": r["scene_id"], "quote": r["quote"], "verdict": "",
        })
    return out

"""Settling the promise ledger: which scene, if any, paid each promise off.

2,842 of 2,886 promises in the real graph are recorded ``open``, across five
*finished* novels that plainly do pay off most of what they set up. Nothing has
ever closed one. Two things break as a result. The thesis ranking divides what
an ending pays by the weight of the whole open ledger, so a denominator inflated
by roughly the entire corpus crushes the evidence term to noise. And the
planner's window onto that ledger fills with setups book three already resolved,
so a continuation is planned around dead material.

Two stages, and the split is the same one entity resolution uses: find the
candidates for nothing, then spend a model call only on the judgment no string
comparison can make.

**This module is the free half.** For each scene, which promises could it
possibly be paying off? Everything here is arithmetic over records already
extracted — no model, no cost, same answer every run.

Three rules were paid for in mistakes and are not negotiable:

*Ask the question per scene, not per pair.* "Does this scene pay this promise"
across every plausible pair is ~260,000 model calls and about $320. "Which of
these promises does this scene pay off", walked over 836 scenes, is 836 calls —
and each scene's text, the expensive part, is sent once instead of seventy
times.

*Never filter a promise down to nothing.* A promise owed by nobody the graph can
resolve — "the narrative", a group, a name that matches no entity — gets every
later scene rather than no scene at all. Filtered strictly it would be silently
never examined, which is indistinguishable from examined-and-unpaid.

*Do not narrow by book, and do not skip by kind.* Both were tried. Confining a
payoff to its plant's own book declares unpayable the 70% of promises owed by
characters who go on appearing, and — because scope is derived from what
survives the pass — a promise planted in book one and paid in book three would
then survive unexamined and be labelled a *series-level* debt for the
continuation to discharge. The filter does not merely miss payoffs; it
manufactures false ones. Skipping ``threat`` and ``prophecy`` is the same
circular guess wearing the extractor's own label.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .db import Graph
from .grounding import _norm
from .profile import SeriesProfile
from .textstats import estimate_tokens


@dataclass
class IndexReport:
    """The shape of the work Stage 2 would have to do."""

    scenes: int = 0
    promises: int = 0
    pairs: int = 0
    unanchored: int = 0
    unowned: int = 0
    per_scene: list[int] = field(default_factory=list)
    prompt_tokens: int = 0

    def cost_usd(self, rate_in: float, *, batch: bool = True) -> float:
        usd = self.prompt_tokens / 1_000_000 * rate_in
        return usd * 0.5 if batch else usd

    def render(self) -> str:
        sizes = sorted(self.per_scene)
        med = statistics.median(sizes) if sizes else 0
        p90 = sizes[int(len(sizes) * 0.9)] if sizes else 0
        lines = [
            f"{self.scenes:,} scenes · {self.promises:,} open promises · {self.pairs:,} candidate pairs",
            f"candidates per scene: median {med:.0f}, p90 {p90}, max {max(sizes) if sizes else 0}",
            f"promises with no resolvable plant (every scene is 'after'): {self.unanchored:,}",
            f"promises owed to nobody resolvable (over-included, never dropped): {self.unowned:,}",
            "",
            f"one pass = {self.scenes:,} calls · {self.prompt_tokens:,} input tokens",
        ]
        for model, rate in (("claude-haiku-4-5", 1.0), ("claude-sonnet-5", 2.0), ("claude-opus-5", 5.0)):
            lines.append(f"  {model:20} ${self.cost_usd(rate):>7,.2f} batched")
        return "\n".join(lines)


def _unj(value: str | None) -> list[str]:
    try:
        out = json.loads(value or "[]")
    except ValueError:
        return []
    return [str(x) for x in out if x] if isinstance(out, list) else []


def candidate_index(graph: Graph, profile: SeriesProfile | None = None) -> tuple[dict[str, list[str]], IndexReport]:
    """For each scene, the open promises that scene could be paying off.

    A promise is a candidate for a scene when the scene comes after the promise
    was planted and somebody the promise is owed by is in it. Both halves fall
    back to over-inclusion rather than exclusion: an unresolvable plant makes
    every scene "after", and an unresolvable debtor makes every later scene a
    candidate.
    """
    from .resolve import norm_name

    scenes = [dict(r) for r in graph.conn.execute(
        "SELECT scene_id, ord, cast_json, pov FROM scenes ORDER BY ord")]
    ord_of = {s["scene_id"]: s["ord"] for s in scenes}

    def canon(name: str) -> str:
        return profile.canonical(name) if profile and name else (name or "")

    # Scene cast is now derived from the events cited to each scene, so it is a
    # participation index rather than the capitalised-word heuristic it used to
    # be, and can be intersected against directly.
    cast_of: dict[str, set[str]] = {}
    for s in scenes:
        cast = {canon(c) for c in _unj(s["cast_json"])}
        if s["pov"]:
            cast.add(canon(s["pov"]))
        cast_of[s["scene_id"]] = {c for c in cast if c}

    # Resolve an owed_by token the same way cast entries were resolved, so the
    # two sides of the intersection are in one namespace. Names, ids and
    # aliases all collapse to the canonical name; a form claimed by more than
    # one entity is not guessed between.
    by_form: dict[str, str] = {}
    normed: dict[str, set[str]] = {}
    for r in graph.conn.execute("SELECT entity_id, name, aliases FROM entities"):
        target = canon(r["name"])
        for key in (r["name"], r["entity_id"], *_unj(r["aliases"])):
            if key:
                by_form.setdefault(key.strip().casefold(), target)
                normed.setdefault(norm_name(key), set()).add(target)

    def resolve(token: str) -> str:
        raw = (token or "").strip()
        if not raw:
            return ""
        hit = by_form.get(raw.casefold())
        if hit:
            return hit
        moved = graph.resolve_entity_id(raw)
        if moved != raw and by_form.get(moved.strip().casefold()):
            return by_form[moved.strip().casefold()]
        claimants = normed.get(norm_name(raw), set())
        return next(iter(claimants)) if len(claimants) == 1 else ""

    report = IndexReport(scenes=len(scenes))
    index: dict[str, list[str]] = {s["scene_id"]: [] for s in scenes}

    for p in graph.conn.execute(
            "SELECT promise_id, planted_in, owed_by FROM promises WHERE status='open'"):
        report.promises += 1

        planted = [s for s in _unj(p["planted_in"]) if s in ord_of]
        if planted:
            after = min(ord_of[s] for s in planted)
        else:
            # No usable plant: the promise has lost its place in the timeline,
            # so every scene is potentially after it. Over-include rather than
            # drop — this is the population `bp extract` now pins to prevent.
            after = float("-inf")
            report.unanchored += 1

        owed = {resolve(t) for t in _unj(p["owed_by"])}
        owed.discard("")
        if not owed:
            report.unowned += 1

        for s in scenes:
            if s["ord"] <= after:
                continue
            if owed and not (owed & cast_of[s["scene_id"]]):
                continue
            index[s["scene_id"]].append(p["promise_id"])

    # What Stage 2 would actually send: each scene's text once, plus a line per
    # candidate promise.
    summaries = {r["promise_id"]: r["summary"] or "" for r in graph.conn.execute(
        "SELECT promise_id, summary FROM promises WHERE status='open'")}
    for s in graph.conn.execute("SELECT scene_id, tokens FROM scenes"):
        ids = index.get(s["scene_id"], [])
        report.per_scene.append(len(ids))
        report.pairs += len(ids)
        listing = sum(estimate_tokens(f"{pid}: {summaries.get(pid, '')}") for pid in ids)
        report.prompt_tokens += int(s["tokens"] or 0) + listing

    return index, report


# ------------------------------------------------------------------- stage two
SETTLE_SYSTEM = """You decide which of a novel's open promises a given scene pays off.

A promise is a setup the text has made: a prophecy, a vow, a foreshadowing beat, a
dangling question. It is PAID when the text delivers the thing that was set up — the
log arrives, the vault opens, the debt is called in.

You are given one scene and a list of promises planted before it. Almost all of them
are unrelated to this scene. Say so by leaving them out.

Close a promise ONLY when this scene's own words show it being paid off, and quote the
line that does it. The quote must be copied exactly from the scene text you were given.
If you cannot point at a line, the answer is no.

Two things to hold on to, because they pull in opposite directions:

A payoff almost never reuses the setup's vocabulary. The setup says "Cyra's log will
reach Sol one day"; the payoff says "the packet finally decoded, eleven years late".
Do not require shared wording, and do not go looking for it.

Sharing a subject is not being paid. A scene that mentions the vault, worries about the
vault, or moves the vault has not opened it. Continuing a thread is not discharging it.
If the promise could still be paid off later, it is not paid here.

Prefer leaving a promise open. An unpaid promise left open costs a little accuracy in a
ledger; a promise wrongly closed erases something the book still owes and leaves no
trace that it did."""


class _Close(BaseModel):
    promise_id: str = Field(description="id from the candidate list, copied exactly")
    quote: str = Field(description="the line in THIS scene that pays it off, verbatim")
    why: str = Field(default="", description="one sentence: what was owed, and how this pays it")
    confidence: float = Field(default=0.5)


class _Settlement(BaseModel):
    closes: list[_Close] = []


@dataclass
class SettleReport:
    """What a settling run found, and what it cost."""

    scenes_read: int = 0
    closes: list[tuple[str, str, str, float]] = field(default_factory=list)
    rejected_quote: list[tuple[str, str, str]] = field(default_factory=list)
    rejected_unknown: list[tuple[str, str]] = field(default_factory=list)
    echoed: int = 0
    unechoed: int = 0
    usd: float = 0.0
    stopped: str = ""
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """The whole result, not the first 25 of it.

        `render` truncates for reading; comparing two runs needs every close,
        and a console tail is not a record. Learned by compromising exactly
        that comparison.
        """
        return {
            "scenes_read": self.scenes_read,
            "usd": round(self.usd, 4),
            "stopped": self.stopped,
            "closes": [{"scene": s, "promise_id": p, "why": w, "confidence": c}
                       for s, p, w, c in self.closes],
            "rejected_quote": [{"scene": s, "promise_id": p, "quote": q}
                               for s, p, q in self.rejected_quote],
            "rejected_unknown": [{"scene": s, "promise_id": p} for s, p in self.rejected_unknown],
            "echoed": self.echoed,
            "unechoed": self.unechoed,
            "errors": self.errors,
        }

    def render(self) -> str:
        lines = [
            f"{self.scenes_read} scenes read · {len(self.closes)} promise(s) closed · ${self.usd:,.2f}",
            f"closes rejected because the quote is not in the scene: {len(self.rejected_quote)}",
            f"closes naming a promise that was not a candidate: {len(self.rejected_unknown)}",
        ]
        # The rejected quote is the interesting one: a model that paraphrases
        # instead of copying looks identical to one that invents, and only the
        # text tells them apart.
        for sid, pid, quote in self.rejected_quote[:5]:
            lines.append(f"  REJECTED {sid} <- {pid}: {quote[:100]!r}")
        if self.closes:
            # work-ed's check: if it only ever closes promises whose own words
            # are echoed in the scene, it is doing string matching in an
            # expensive costume rather than reading.
            total = self.echoed + self.unechoed
            lines.append(
                f"of the closes, {self.unechoed} ({self.unechoed / total:.0%}) were on scenes that do "
                f"NOT echo the promise's own distinctive words")
        lines += [f"  {sid}  <-  {pid}  ({conf:.2f}) {why[:70]}"
                  for sid, pid, why, conf in self.closes[:25]]
        if self.stopped:
            lines.append(f"STOPPED: {self.stopped}")
        lines += [f"error: {e}" for e in self.errors[:8]]
        return "\n".join(lines)


def _close(graph, promise_id: str, scene_id: str, run_id: str, confidence: float) -> None:
    """Mark a promise paid, leaving behind what it was and which run did it.

    Closing overwrites `status` and `paid_in` on a record that was open, and we
    will correct this settler's prompt at least once. Without the trail there is
    no way to ask "which closes came from the run before the fix" — the same
    hole that made 156 wrong demotions an archaeology exercise.
    """
    before = graph.conn.execute(
        "SELECT status, paid_in FROM promises WHERE promise_id=?", (promise_id,)).fetchone()
    if before is None:
        return
    for fieldname, new in (("status", "paid"), ("paid_in", scene_id)):
        if before[fieldname] != new:
            graph.record_change(table="promises", record_id=promise_id, field=fieldname,
                                old=before[fieldname], new=new, run_id=run_id,
                                reason=f"settled at confidence {confidence:.2f}")
    graph.conn.execute("UPDATE promises SET status='paid', paid_in=? WHERE promise_id=?",
                       (scene_id, promise_id))


def _decode_escapes(text: str) -> str:
    """Turn a literal ``\\u2019`` back into the character it stands for.

    The model quotes faithfully but the escape sometimes survives decoding as
    six literal characters, and prose is full of curly apostrophes. Left alone
    this rejected 5 of 6 proposed closes in the pilot as unverifiable — the
    verification guard manufacturing exactly the fabrication it exists to
    catch. Nothing else is touched: a quote that is genuinely not in the scene
    still fails.
    """
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text or "")


def _distinctive(text: str) -> set[str]:
    from .grounding import _STOP, _WORD

    return {w for w in (x.lower() for x in _WORD.findall(text)) if w not in _STOP and len(w) > 4}


def settle(
    graph: Graph, profile: SeriesProfile, client, *, model: str,
    scene_ids: list[str] | None = None, max_usd: float = 1.0,
    apply: bool = False, run_id: str = "", progress=lambda _s: None,
) -> SettleReport:
    """Ask each scene which of its candidate promises it pays off.

    Walks scenes in reading order and drops a promise from every later scene's
    list the moment something closes it. A promise only needs paying once, and
    the earliest payoff is the right one — so this is free accuracy as well as
    the only lossless way to shrink the candidate listing, which is 96% of what
    a run costs.

    Two guards, both cheap. A close whose quote is not literally in the scene
    is rejected here rather than trusted: the same exact test `bp ground` uses,
    and the same defect it found 391 of. And a close naming a promise that was
    not on that scene's list is rejected outright.

    Dry by default. `max_usd` is enforced in this loop because the run does not
    go through `extract`, where the existing cap lives.
    """
    from .llm import Usage, rate_for, structured

    run_id = run_id or f"settle-{time.strftime('%Y%m%d-%H%M%S')}-{model}"
    index, _ = candidate_index(graph, profile)
    rows = {r["promise_id"]: dict(r) for r in graph.conn.execute(
        "SELECT promise_id, summary FROM promises WHERE status='open'")}

    scenes = [dict(r) for r in graph.conn.execute(
        "SELECT scene_id, text, tokens FROM scenes ORDER BY ord")]
    if scene_ids is not None:
        keep = set(scene_ids)
        scenes = [s for s in scenes if s["scene_id"] in keep]

    report = SettleReport()
    usage = Usage()
    closed: set[str] = set()

    for scene in scenes:
        candidates = [p for p in index.get(scene["scene_id"], []) if p not in closed]
        if not candidates:
            continue
        rate_in, _ = rate_for(model)
        listing = "\n".join(f"- {pid}: {rows[pid]['summary']}" for pid in candidates if pid in rows)
        projected = usage.usd + estimate_tokens(listing + scene["text"]) / 1e6 * rate_in
        if projected > max_usd:
            report.stopped = (f"spend cap ${max_usd:,.2f} would be exceeded "
                              f"(${usage.usd:,.2f} spent, {report.scenes_read} scenes read)")
            break

        prompt = (f"--- SCENE {scene['scene_id']} ---\n{scene['text']}\n\n"
                  f"--- PROMISES PLANTED BEFORE THIS SCENE ---\n{listing}")
        try:
            out = structured(client, model, _Settlement, system=SETTLE_SYSTEM, prompt=prompt,
                             max_tokens=4_000, usage=usage, stage="settle")
        except Exception as exc:
            report.errors.append(f"{scene['scene_id']}: {type(exc).__name__}: {exc}")
            continue
        report.scenes_read += 1

        scene_norm = _norm(scene["text"])
        for c in out.closes:
            if c.promise_id not in set(candidates):
                report.rejected_unknown.append((scene["scene_id"], c.promise_id))
                continue
            nq = _norm(_decode_escapes(c.quote))
            if not nq or nq not in scene_norm:
                report.rejected_quote.append((scene["scene_id"], c.promise_id, c.quote))
                continue
            closed.add(c.promise_id)
            report.closes.append((scene["scene_id"], c.promise_id, c.why, c.confidence))
            shared = _distinctive(rows[c.promise_id]["summary"]) & _distinctive(scene["text"])
            if shared:
                report.echoed += 1
            else:
                report.unechoed += 1
            if apply:
                _close(graph, c.promise_id, scene["scene_id"], run_id, c.confidence)
        progress(f"  {scene['scene_id']}: {len(candidates)} candidates, "
                 f"{len(out.closes)} proposed, ${usage.usd:,.2f} so far")

    report.usd = usage.usd
    if apply:
        graph.conn.commit()
    return report


#: `estimate_tokens` is a words-to-tokens approximation and it runs light on
#: this shape of prompt — a long list of short, id-heavy lines. Measured
#: against the pilot it under-reported by about 2.3x once output tokens were
#: counted too. A spend cap checked against an estimate that optimistic is not
#: a cap, so the estimate carries the measured factor and the cap is applied to
#: the honest number. Re-measure this if the prompt shape changes.
_ESTIMATE_SLACK = 2.3


def settle_batch(
    graph: Graph, profile: SeriesProfile, client, *, model: str,
    scene_ids: list[str] | None = None, max_usd: float = 10.0,
    apply: bool = False, run_id: str = "", progress=lambda _s: None,
) -> SettleReport:
    """The same question, submitted through the Batch API at half price.

    One trade is unavoidable here. Batching submits every scene at once, so a
    promise closed in scene 3 is still offered to scene 40 — the drop-as-closed
    saving does not apply within a batch. What it costs in tokens it more than
    returns in price, and the saving it forgoes can be measured after the fact
    from the closes themselves rather than paid for to discover.

    Estimated before submitting and refused if it would exceed ``max_usd``,
    because a batch cannot be stopped halfway once it is running.
    """
    from .extract import _custom_id
    from .llm import BATCH_DISCOUNT, Usage, poll_batch, rate_for, submit_batch

    run_id = run_id or f"settle-{time.strftime('%Y%m%d-%H%M%S')}-{model}-batch"
    index, _ = candidate_index(graph, profile)
    rows = {r["promise_id"]: dict(r) for r in graph.conn.execute(
        "SELECT promise_id, summary FROM promises WHERE status='open'")}
    scenes = [dict(r) for r in graph.conn.execute(
        "SELECT scene_id, text, tokens FROM scenes ORDER BY ord")]
    if scene_ids is not None:
        keep = set(scene_ids)
        scenes = [s for s in scenes if s["scene_id"] in keep]

    schema = _Settlement.model_json_schema()
    tool = {"name": "emit", "description": "Emit a Settlement.", "input_schema": schema}
    requests, prompts = [], {}
    for s in scenes:
        cands = [p for p in index.get(s["scene_id"], []) if p in rows]
        if not cands:
            continue
        listing = "\n".join(f"- {p}: {rows[p]['summary']}" for p in cands)
        prompt = (f"--- SCENE {s['scene_id']} ---\n{s['text']}\n\n"
                  f"--- PROMISES PLANTED BEFORE THIS SCENE ---\n{listing}")
        cid = _custom_id("settle", s["scene_id"])
        prompts[cid] = (s, set(cands))
        requests.append({
            "custom_id": cid,
            "params": {
                "model": model, "max_tokens": 4_000,
                "system": [{"type": "text", "text": SETTLE_SYSTEM}],
                "tools": [tool], "tool_choice": {"type": "tool", "name": "emit"},
                "messages": [{"role": "user", "content": prompt}],
            },
        })

    report = SettleReport()
    if len(prompts) != len(requests):
        raise ValueError("two scene ids collide as one batch custom_id")

    rate_in, _rate_out = rate_for(model)
    estimate = (sum(estimate_tokens(r["params"]["messages"][0]["content"])
                    for r in requests) / 1e6 * rate_in * BATCH_DISCOUNT) * _ESTIMATE_SLACK
    progress(f"  {len(requests)} scenes · estimated ${estimate:,.2f} batched")
    if estimate > max_usd:
        report.stopped = (f"estimated ${estimate:,.2f} exceeds the ${max_usd:,.2f} cap; "
                          f"a batch cannot be stopped once submitted, so it was not sent")
        return report

    batch_id = submit_batch(client, requests)
    progress(f"  batch {batch_id} submitted; polling")
    usage = Usage()
    for result in poll_batch(client, batch_id):
        entry = prompts.get(result.custom_id)
        if entry is None:
            continue
        scene, allowed = entry
        if result.result.type != "succeeded":
            report.errors.append(f"{scene['scene_id']}: {result.result.type}")
            continue
        msg = result.result.message
        usage.add(model, msg.usage, stage="settle", batch=True)
        report.scenes_read += 1
        payload = next((b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        if payload is None:
            continue
        try:
            out = _Settlement.model_validate(payload)
        except Exception as exc:
            report.errors.append(f"{scene['scene_id']}: {type(exc).__name__}: {exc}")
            continue

        scene_norm = _norm(scene["text"])
        for c in out.closes:
            if c.promise_id not in allowed:
                report.rejected_unknown.append((scene["scene_id"], c.promise_id))
                continue
            nq = _norm(_decode_escapes(c.quote))
            if not nq or nq not in scene_norm:
                report.rejected_quote.append((scene["scene_id"], c.promise_id, c.quote))
                continue
            report.closes.append((scene["scene_id"], c.promise_id, c.why, c.confidence))
            shared = _distinctive(rows[c.promise_id]["summary"]) & _distinctive(scene["text"])
            if shared:
                report.echoed += 1
            else:
                report.unechoed += 1
            if apply:
                _close(graph, c.promise_id, scene["scene_id"], run_id, c.confidence)

    report.usd = usage.usd
    if apply:
        graph.conn.commit()
    return report

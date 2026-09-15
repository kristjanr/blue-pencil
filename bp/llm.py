"""The one place the engine talks to a model.

Everything model-shaped is funnelled through here so the awkward parts live in
one file rather than nine: prompt caching on the stable half of a context pack,
structured outputs via tool use, the Batch API for extraction, streaming for
long drafts, and the ``refusal`` stop reason.

That last one is not a footnote. The genre is violent; a refusal mid-chapter
must never become a silent blank in the manuscript. So a refusal raises, loudly,
after a fallback attempt, and the caller decides.

Nothing here is required for the deterministic half of the engine. Ingestion,
the hard checkers, the repetition auditor and the shape metrics all run with no
API key at all — which is what makes it possible to develop the editor in a
loop.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence, Type, TypeVar

from pydantic import BaseModel

from .errors import NoCredentials, Refused
from .textstats import estimate_tokens

T = TypeVar("T", bound=BaseModel)

#: First-party rates, USD per million tokens (input, output). Used for the
#: run-budget cap and the cost report; an estimate, not a quote.
RATES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
#: Rate to fall back on for a model this table does not know. Deliberately at
#: the top of the range: an unknown model that turns out to be expensive should
#: trip `budget.max_usd` early rather than late.
DEFAULT_RATE = (10.0, 50.0)

#: Cached input bills at a fraction of the input rate. A tenth on most models;
#: Claude Fable 5.1 reads at $0.25/MTok, which is a fortieth of its input rate.
CACHE_READ_DISCOUNT = 0.1
CACHE_READ_DISCOUNT_BY_MODEL = {"claude-fable-5-1": 0.025}
CACHE_WRITE_PREMIUM = 1.25    # 5-minute TTL; a 1-hour TTL writes at 2x
BATCH_DISCOUNT = 0.5          # asynchronous batch work is half price

#: Minimum cacheable prefix, in tokens, by model. A shorter prefix silently
#: does not cache — no error, just `cache_creation_input_tokens: 0` — so a
#: breakpoint below the minimum is not merely useless, it burns one of the four
#: slots a request is allowed. The numbers are NOT monotonic across
#: generations: 512 on the newest models, 4096 on Haiku 4.5.
MIN_CACHEABLE_TOKENS: dict[str, int] = {
    "claude-fable-5-1": 512,
    "claude-fable-5": 512,
    "claude-opus-5": 512,
    "claude-opus-4-8": 1024,
    "claude-sonnet-5": 1024,
    "claude-sonnet-4-6": 1024,
    "claude-haiku-4-5": 4096,
}
DEFAULT_MIN_CACHEABLE = 1024

#: Most breakpoints a single request may carry.
MAX_CACHE_BREAKPOINTS = 4


def _base_id(model: str) -> str:
    """Strip a trailing date snapshot, so `claude-haiku-4-5-20251001` still
    finds its row. Current model ids carry no suffix, but one left over in a
    config should degrade to the right rate rather than to the default."""
    import re

    return re.sub(r"-\d{8}$", "", (model or "").strip())


def rate_for(model: str) -> tuple[float, float]:
    return RATES.get(_base_id(model), DEFAULT_RATE)


def cache_read_discount(model: str) -> float:
    return CACHE_READ_DISCOUNT_BY_MODEL.get(_base_id(model), CACHE_READ_DISCOUNT)


def min_cacheable_tokens(model: str) -> int:
    return MIN_CACHEABLE_TOKENS.get(_base_id(model), DEFAULT_MIN_CACHEABLE)


def make_client(api_key: str | None = None):
    """An Anthropic client, or a clear error about why there isn't one."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise NoCredentials(
            "no ANTHROPIC_API_KEY set. The deterministic half of Blue Pencil "
            "(ingest, hard checks, repetition, shape metrics) runs without one; "
            "extraction, planning and drafting do not."
        )
    import anthropic

    return anthropic.Anthropic(api_key=key)


@dataclass
class Usage:
    """Running token and cost tally, so a run can respect ``budget.max_usd``."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    usd: float = 0.0
    calls: int = 0
    by_stage: dict[str, float] = field(default_factory=dict)

    def add(self, model: str, usage: Any, *, stage: str = "", batch: bool = False) -> None:
        rate_in, rate_out = rate_for(model)
        read_discount = cache_read_discount(model)
        n_in = int(getattr(usage, "input_tokens", 0) or 0)
        n_out = int(getattr(usage, "output_tokens", 0) or 0)
        n_cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        n_cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)

        cost = (
            n_in * rate_in
            + n_cache_read * rate_in * read_discount
            + n_cache_write * rate_in * CACHE_WRITE_PREMIUM
            + n_out * rate_out
        ) / 1_000_000
        if batch:
            cost *= BATCH_DISCOUNT

        self.input_tokens += n_in
        self.output_tokens += n_out
        self.cache_read_tokens += n_cache_read
        self.cache_write_tokens += n_cache_write
        self.usd += cost
        self.calls += 1
        if stage:
            self.by_stage[stage] = self.by_stage.get(stage, 0.0) + cost

    def render(self) -> str:
        lines = [
            f"{self.calls} calls · {self.input_tokens:,} in "
            f"({self.cache_read_tokens:,} cached) · {self.output_tokens:,} out · ${self.usd:,.2f}"
        ]
        for stage, cost in sorted(self.by_stage.items(), key=lambda t: -t[1]):
            lines.append(f"  {stage}: ${cost:,.2f}")
        return "\n".join(lines)


def _retry(fn, *, attempts: int = 4, base: float = 2.0):
    """Exponential backoff with jitter on transient failures."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # SDK raises typed errors; retry the retryable
            name = type(exc).__name__
            if name in {"BadRequestError", "AuthenticationError", "PermissionDeniedError", "NotFoundError"}:
                raise
            last = exc
            if i == attempts - 1:
                break
            time.sleep(base ** i + random.random())
    raise last  # type: ignore[misc]


def _text_blocks(blocks: Sequence[str]) -> list[dict]:
    return [{"type": "text", "text": t} for t in blocks if t]


def schema_tokens(schema: dict) -> int:
    """Rough size of a tool schema as the API will render it."""
    return estimate_tokens(json.dumps(schema))


def _cacheable(
    blocks: Sequence[str], *, model: str, ttl: str | None = None, prefix_tokens: int = 0
) -> list[dict]:
    """Render text blocks, breakpointing the last one **iff** it can cache.

    Two rules, both learned the hard way:

    *Place the breakpoint at the end of the stable prefix, not the end of the
    prompt.* Caching is a prefix match, and a read can only happen at a
    breakpoint. A breakpoint sitting after per-request content gives the next
    request nothing to read: it shares the first N tokens, but there is no read
    point at N, so the whole prefix is reprocessed at full price. The symptom is
    `cache_creation_input_tokens` on every request and `cache_read_input_tokens`
    that never covers the shared part.

    *Below the model's minimum, do not mark at all.* A short prefix does not
    cache and says nothing about it — no error, just a zero. The marker is then
    worse than useless, because a request may carry only four breakpoints and
    that one is spent.

    ``prefix_tokens`` is what renders *ahead* of these blocks and therefore
    falls inside the cached prefix too. Render order is ``tools`` -> ``system``
    -> ``messages``, so a breakpoint on the last system block caches the tool
    definitions with it. Extraction is the case that makes this matter: its
    system text is ~175 tokens and its tool schema is ~1,200, so measuring the
    text alone would call the prefix uncacheable and drop a breakpoint that is
    in fact serving most of the request from cache.
    """
    out = _text_blocks(blocks)
    if not out:
        return out
    total = prefix_tokens + sum(estimate_tokens(b["text"]) for b in out)
    if total < min_cacheable_tokens(model):
        return out
    control: dict[str, Any] = {"type": "ephemeral"}
    if ttl:
        control["ttl"] = ttl
    out[-1]["cache_control"] = control
    return out


def caches(blocks: Sequence[str], *, model: str, prefix_tokens: int = 0) -> bool:
    """Whether a breakpoint over these blocks would actually cache."""
    total = prefix_tokens + sum(estimate_tokens(b) for b in blocks if b)
    return total >= min_cacheable_tokens(model)


#: Models that reject the sampling parameters (temperature/top_p/top_k) with a
#: 400. Everything current except Haiku; sending temperature to one of these
#: fails the request outright, and the value we send is the default anyway.
_NO_SAMPLING = ("claude-fable-", "claude-mythos-", "claude-opus-", "claude-sonnet-5")


def _accepts_sampling(model: str) -> bool:
    return not model.startswith(_NO_SAMPLING)


#: Validation keywords a strict tool schema rejects. Pydantic emits them from
#: ordinary field constraints — ``Field(ge=0, le=1)`` becomes minimum/maximum —
#: so a schema that is perfectly valid JSON Schema is refused with a 400.
_STRICT_UNSUPPORTED = frozenset({
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "format",
    "minItems", "maxItems", "uniqueItems",
})


def _strict_schema(schema: dict) -> dict:
    """Make a pydantic schema acceptable to a strict tool.

    Two edits. Every object is closed, because a strict tool with an open
    object is rejected. And the validation keywords strict does not support are
    dropped — the bound is enforced by pydantic on the way back in either way,
    so nothing is actually lost by not declaring it on the way out.
    """
    if isinstance(schema, dict):
        out = {k: _strict_schema(v) for k, v in schema.items() if k not in _STRICT_UNSUPPORTED}
        if out.get("type") == "object" and "additionalProperties" not in out:
            out["additionalProperties"] = False
        return out
    if isinstance(schema, list):
        return [_strict_schema(v) for v in schema]  # type: ignore[return-value]
    return schema


# ------------------------------------------------------------------ structured
def structured(
    client,
    model: str,
    schema: Type[T],
    *,
    system: str | Sequence[str] = "",
    prompt: str,
    max_tokens: int = 8_000,
    usage: Usage | None = None,
    stage: str = "",
    temperature: float | None = None,
) -> T:
    """One structured record, validated against a pydantic schema.

    Chapter cards and every graph record go through this: nothing downstream
    should ever be parsing prose.
    """
    raw_schema = schema.model_json_schema()
    # `strict` keeps the arguments schema-valid now that the call is no longer
    # forced (see the tool_choice note below), but it caps how complex a schema
    # may be, and the extraction records exceed that cap. It is an optimisation,
    # not a correctness requirement — the result is validated against the model
    # on the way back in regardless — so fall back rather than fail.
    strict_tool = {
        "name": "emit",
        "description": f"Emit one {schema.__name__} record.",
        "input_schema": _strict_schema(raw_schema),
        "strict": True,
    }
    plain_tool = {
        "name": "emit",
        "description": f"Emit one {schema.__name__} record.",
        "input_schema": raw_schema,
    }
    tool: dict[str, Any] = strict_tool
    # Tools render before system, so the breakpoint on the last system block
    # covers the tool schema as well — and here the schema is most of what
    # there is to cache. Counting only the system text would put a 175-token
    # block under every minimum and drop a breakpoint that is doing real work.
    system_blocks = _cacheable(
        [system] if isinstance(system, str) else list(system),
        model=model, prefix_tokens=schema_tokens(raw_schema),
    ) if system else []

    def call():
        kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=max_tokens,
            tools=[tool],
            # Forced tool use (`{"type": "tool"}` / `{"type": "any"}`) is rejected
            # with a 400 on the newest models — including the one `plan` runs on.
            # `auto` plus an instruction naming the tool is the supported shape.
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content":
                       f"{prompt}\n\nReply by calling the `emit` tool exactly once. "
                       f"Do not answer in prose."}],
        )
        if system_blocks:
            kwargs["system"] = system_blocks
        if temperature is not None and _accepts_sampling(model):
            kwargs["temperature"] = temperature
        return client.messages.create(**kwargs)

    try:
        msg = _retry(call)
    except Exception as exc:
        # A schema the strict path will not accept is a property of the schema,
        # not of this scene, so retrying it strictly is wasted. Drop to the
        # plain tool once and let every later call in this run use it too.
        if "schema" not in str(exc).lower():
            raise
        tool = plain_tool
        msg = _retry(call)

    def _emitted(m):
        for block in m.content:
            if getattr(block, "type", "") == "tool_use":
                return block.input
        return None

    if usage is not None:
        usage.add(model, msg.usage, stage=stage)
    if getattr(msg, "stop_reason", "") == "refusal":
        raise Refused(f"{model} refused a structured request ({stage or 'unstaged'})")

    payload = _emitted(msg)
    if payload is None and tool is strict_tool:
        # A strict schema is not always rejected loudly. Sometimes the call
        # succeeds and the model simply declines to emit, which used to fall
        # through to "no structured output" and lose the work. Same remedy as
        # the loud case: drop to the plain tool and ask once more.
        tool = plain_tool
        msg = _retry(call)
        if usage is not None:
            usage.add(model, msg.usage, stage=stage)
        payload = _emitted(msg)
    if payload is None:
        raise ValueError(f"{model} returned no structured output for {schema.__name__}")
    return schema.model_validate(payload)


def structured_many(
    client, model: str, schema: Type[T], *, system: str | Sequence[str] = "", prompt: str,
    max_tokens: int = 16_000, usage: Usage | None = None, stage: str = "",
) -> list[T]:
    """A list of records in one call, for extraction passes that yield many."""

    class Wrapper(BaseModel):
        items: list[schema]  # type: ignore[valid-type]

    Wrapper.__name__ = f"{schema.__name__}List"
    return structured(client, model, Wrapper, system=system, prompt=prompt,
                      max_tokens=max_tokens, usage=usage, stage=stage).items  # type: ignore[attr-defined]


# ----------------------------------------------------------------------- prose
def write(
    client,
    model: str,
    *,
    stable_blocks: Sequence[str],
    tail_blocks: Sequence[str] = (),
    prompt: str,
    max_tokens: int = 16_000,
    usage: Usage | None = None,
    stage: str = "draft",
    thinking_effort: str | None = None,
    temperature: float = 1.0,
    fallback_model: str | None = None,
) -> str:
    """Long prose, streamed, with caching placed where it pays and refusals handled.

    The context pack arrives split in two, because the two halves have different
    lifetimes and want different treatment:

    ``stable_blocks``
        Unchanged for the whole book — engine rules, series bible digest, POV
        technique spec. These get the explicit breakpoint, so every later scene
        in the book reads them back instead of paying for them again. This is
        the half the pack's render order exists to protect.

    ``tail_blocks``
        The chapter card, the POV's knowledge state, exemplars, recent chapters,
        the phrase ledger. Same across the candidates of one scene, different
        next chapter. Top-level auto-caching moves a breakpoint along behind
        these, which earns its keep across the N candidates and costs nothing
        when it does not.

    Putting the only breakpoint after the tail — the shape this had before —
    means the stable half never gets a read point and is reprocessed at full
    price on every scene of every chapter.
    """
    stable = _cacheable(stable_blocks, model=model)
    system = stable + _text_blocks(tail_blocks)
    # Auto-caching is worth requesting only if the whole prompt clears the
    # minimum; below it the field is a silent no-op that spends a slot.
    auto_cache = caches(list(stable_blocks) + list(tail_blocks), model=model)

    def call(m: str) -> Any:
        kwargs: dict[str, Any] = dict(
            model=m,
            max_tokens=max_tokens,
            system=_cacheable(stable_blocks, model=m) + _text_blocks(tail_blocks),
            messages=[{"role": "user", "content": prompt}],
        )
        if auto_cache and tail_blocks:
            # Skipped when there is no tail: the explicit marker is then already
            # on the last block, and a same-TTL top-level field would be a no-op.
            kwargs["cache_control"] = {"type": "ephemeral"}
        if _accepts_sampling(m):
            kwargs["temperature"] = temperature
        if thinking_effort:
            # Adaptive thinking replaced the fixed-budget form; effort is its own
            # setting under output_config, not a key inside `thinking`.
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": thinking_effort}
            kwargs.pop("temperature", None)
        with client.messages.stream(**kwargs) as stream:
            for _ in stream.text_stream:
                pass
            return stream.get_final_message()

    msg = _retry(lambda: call(model))
    if usage is not None:
        usage.add(model, msg.usage, stage=stage)

    if getattr(msg, "stop_reason", "") == "refusal":
        if fallback_model and fallback_model != model:
            msg = _retry(lambda: call(fallback_model))
            if usage is not None:
                usage.add(fallback_model, msg.usage, stage=stage + ":fallback")
            if getattr(msg, "stop_reason", "") != "refusal":
                return _text_of(msg)
        raise Refused(
            f"{model} refused this scene. Never let a refused scene become a blank in the "
            f"manuscript — re-plan the beat, split the scene, or write it by hand."
        )
    return _text_of(msg)


def _text_of(msg) -> str:
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


# ----------------------------------------------------------------------- batch
def submit_batch(client, requests: list[dict]) -> str:
    """Submit extraction work asynchronously. Half price, and extraction is the
    one stage where latency genuinely does not matter."""
    batch = _retry(lambda: client.messages.batches.create(requests=requests))
    return batch.id


def poll_batch(client, batch_id: str, *, interval: float = 20.0, timeout: float = 86_400.0) -> Iterable[Any]:
    waited = 0.0
    while True:
        batch = _retry(lambda: client.messages.batches.retrieve(batch_id))
        if batch.processing_status == "ended":
            break
        if waited > timeout:
            raise TimeoutError(f"batch {batch_id} still running after {timeout:.0f}s")
        time.sleep(interval)
        waited += interval
    return client.messages.batches.results(batch_id)


# ------------------------------------------------------------------- judgements
def classify_mentions(client, model: str, text: str, candidates: list[dict]) -> dict[str, bool]:
    """Precision pass over lexical mention candidates. Never adds mentions."""

    class Verdict(BaseModel):
        id: str
        refers: bool
        why: str = ""

    class Verdicts(BaseModel):
        verdicts: list[Verdict]

    listing = "\n".join(f"- id={c['id']} · event: {c['label']}\n  near: {c['excerpt']}" for c in candidates)
    prompt = (
        "Below is a chapter draft, then a list of candidate references to known events.\n"
        "For each candidate, say whether the draft actually refers to that event — as opposed "
        "to merely sharing vocabulary with it. Do not add candidates.\n\n"
        f"--- DRAFT ---\n{text[:20000]}\n\n--- CANDIDATES ---\n{listing}"
    )
    result = structured(client, model, Verdicts, prompt=prompt, max_tokens=4000, stage="checks")
    return {v.id: v.refers for v in result.verdicts}


def discriminate(client, model: str, *, candidate: str, canon: list[str]) -> tuple[float, str]:
    """Blind judge. Returns (confidence it is generated, reason)."""

    class Verdict(BaseModel):
        generated_confidence: float
        reason: str

    passages = "\n\n".join(f"--- PASSAGE {chr(65+i)} ---\n{c}" for i, c in enumerate(canon))
    prompt = (
        "Some of these passages are from a published novel and one is machine-generated.\n"
        "Judge the LAST passage only. How confident are you (0..1) that it is machine-generated, "
        "and what specifically gives it away? If nothing does, say so and score low.\n\n"
        f"{passages}\n\n--- LAST PASSAGE ---\n{candidate}"
    )
    v = structured(client, model, Verdict, prompt=prompt, max_tokens=1500, stage="checks")
    return max(0.0, min(1.0, v.generated_confidence)), v.reason


def reader_panel(client, model: str, text: str, personas: dict[str, str]) -> dict[str, dict]:
    """Three personas, scored separately and never averaged."""

    class Report(BaseModel):
        persona: str
        scores: dict[str, int]
        comment: str
        quote: str = ""

    class Panel(BaseModel):
        reports: list[Report]

    brief = "\n".join(f"- {name}: {desc}" for name, desc in personas.items())
    prompt = (
        "Read the chapter below, then report as EACH of these readers independently. "
        "Do not reconcile them — where they disagree, let them disagree.\n"
        "Score 0–10 on: boredom (10 = gripping), exposition (10 = well handled), payoff (10 = earned).\n\n"
        f"{brief}\n\n--- CHAPTER ---\n{text}"
    )
    panel = structured(client, model, Panel, prompt=prompt, max_tokens=4000, stage="checks")
    return {r.persona: {"scores": r.scores, "comment": r.comment, "quote": r.quote} for r in panel.reports}

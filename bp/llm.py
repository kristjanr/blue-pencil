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

T = TypeVar("T", bound=BaseModel)

#: Approximate first-party rates, USD per million tokens (input, output).
#: Used only for the run-budget estimate and the cost report; not a quote.
RATES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (15.0, 75.0),
    "claude-fable-5-1": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
CACHE_READ_DISCOUNT = 0.1     # cached input tokens bill at roughly a tenth
BATCH_DISCOUNT = 0.5          # asynchronous batch work is half price


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
        rate_in, rate_out = RATES.get(model, (5.0, 25.0))
        n_in = int(getattr(usage, "input_tokens", 0) or 0)
        n_out = int(getattr(usage, "output_tokens", 0) or 0)
        n_cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        n_cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)

        cost = (
            n_in * rate_in
            + n_cache_read * rate_in * CACHE_READ_DISCOUNT
            + n_cache_write * rate_in * 1.25
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


def _cacheable(blocks: Sequence[str]) -> list[dict]:
    """Mark the last stable block with a cache breakpoint.

    The context pack has a fixed render order for exactly this reason: engine
    rules, bible digest and style spec do not change within a book, so they are
    written once and read back at a tenth of the price for every scene after.
    """
    out: list[dict] = []
    for i, text in enumerate(blocks):
        block: dict[str, Any] = {"type": "text", "text": text}
        if i == len(blocks) - 1:
            block["cache_control"] = {"type": "ephemeral"}
        out.append(block)
    return out


#: Models that reject the sampling parameters (temperature/top_p/top_k) with a
#: 400. Everything current except Haiku; sending temperature to one of these
#: fails the request outright, and the value we send is the default anyway.
_NO_SAMPLING = ("claude-fable-", "claude-mythos-", "claude-opus-", "claude-sonnet-5")


def _accepts_sampling(model: str) -> bool:
    return not model.startswith(_NO_SAMPLING)


def _strict_schema(schema: dict) -> dict:
    """Close every object in a JSON schema, which `strict` requires.

    Pydantic leaves objects open; a strict tool with an open object is rejected.
    """
    if isinstance(schema, dict):
        out = {k: _strict_schema(v) for k, v in schema.items()}
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
    tool = {
        "name": "emit",
        "description": f"Emit one {schema.__name__} record.",
        "input_schema": _strict_schema(schema.model_json_schema()),
        # `strict` is what keeps the arguments schema-valid now that the call is
        # no longer forced; see the tool_choice note below.
        "strict": True,
    }
    system_blocks = _cacheable([system] if isinstance(system, str) else list(system)) if system else []

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

    msg = _retry(call)
    if usage is not None:
        usage.add(model, msg.usage, stage=stage)
    if getattr(msg, "stop_reason", "") == "refusal":
        raise Refused(f"{model} refused a structured request ({stage or 'unstaged'})")
    for block in msg.content:
        if getattr(block, "type", "") == "tool_use":
            return schema.model_validate(block.input)
    raise ValueError(f"{model} returned no structured output for {schema.__name__}")


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
    system_blocks: Sequence[str],
    prompt: str,
    max_tokens: int = 16_000,
    usage: Usage | None = None,
    stage: str = "draft",
    thinking_effort: str | None = None,
    temperature: float = 1.0,
    fallback_model: str | None = None,
) -> str:
    """Long prose, streamed, with the refusal path handled.

    ``system_blocks`` is the context pack in render order; the last block gets
    the cache breakpoint.
    """
    def call(m: str) -> Any:
        kwargs: dict[str, Any] = dict(
            model=m,
            max_tokens=max_tokens,
            system=_cacheable(system_blocks),
            messages=[{"role": "user", "content": prompt}],
        )
        if _accepts_sampling(m):
            kwargs["temperature"] = temperature
        if thinking_effort:
            # Adaptive thinking replaced the fixed-budget form; effort is its own
            # setting under output_config, not a key inside `thinking`. The old
            # `{"type": "enabled", ...}` shape is a 400 on every model configured
            # here, which would have failed every drafting call.
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

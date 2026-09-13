"""What we actually send to the API: cache placement, minimums, and rates.

These are shape tests against a fake client. They cannot prove the API accepts
the request — there is no key here — but they pin the three things that were
silently wrong and cost money rather than raising: a breakpoint after the
volatile tail, breakpoints under the minimum cacheable prefix, and a rate table
that overstated every model.
"""

from types import SimpleNamespace

import pytest

from bp import llm
from bp.llm import Usage, caches, min_cacheable_tokens, rate_for, write


class FakeStream:
    def __init__(self, captured, kwargs):
        captured.append(kwargs)
        self.text_stream = iter(["some prose"])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="some prose")],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0),
        )


class FakeClient:
    def __init__(self):
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(stream=lambda **kw: FakeStream(self.calls, kw))


def _blocks(tokens: int) -> str:
    """A block of roughly `tokens` estimated tokens."""
    return "word " * int(tokens * 3.8 / 5)


# ─────────────────────────────────────────────────── breakpoint placement
def test_breakpoint_lands_on_the_stable_prefix_not_the_volatile_tail():
    """The bug this test exists for: a breakpoint after per-chapter content
    gives the next chapter nothing to read, so the stable prefix is reprocessed
    at full price on every scene."""
    client = FakeClient()
    write(client, "claude-opus-5",
          stable_blocks=[_blocks(400), _blocks(400)],
          tail_blocks=[_blocks(300), _blocks(300)],
          prompt="write the scene")

    system = client.calls[0]["system"]
    marked = [i for i, b in enumerate(system) if "cache_control" in b]
    assert marked == [1], "the breakpoint must sit on the last STABLE block"
    assert len(system) == 4, "tail blocks are still sent, just after the breakpoint"
    assert all("cache_control" not in b for b in system[2:])


def test_tail_gets_top_level_auto_caching():
    """Worth its slot across the N candidates of one scene, free when it isn't."""
    client = FakeClient()
    write(client, "claude-opus-5", stable_blocks=[_blocks(600)],
          tail_blocks=[_blocks(600)], prompt="x")
    assert client.calls[0]["cache_control"] == {"type": "ephemeral"}


def test_no_top_level_field_when_there_is_no_tail():
    """The explicit marker is already on the last block; a same-TTL top-level
    field would be a no-op."""
    client = FakeClient()
    write(client, "claude-opus-5", stable_blocks=[_blocks(900)], prompt="x")
    assert "cache_control" not in client.calls[0]


# ─────────────────────────────────────────────────── the silent minimum
def test_no_breakpoint_below_the_models_minimum():
    """Under the minimum a breakpoint caches nothing, reports nothing, and
    spends one of the four slots a request is allowed."""
    client = FakeClient()
    write(client, "claude-opus-5", stable_blocks=[_blocks(100)], prompt="x")
    assert all("cache_control" not in b for b in client.calls[0]["system"])
    assert "cache_control" not in client.calls[0]


def test_the_minimum_is_per_model_and_not_monotonic():
    """A 3k-token prefix caches on Opus 5 and silently does not on Haiku 4.5."""
    assert min_cacheable_tokens("claude-opus-5") == 512
    assert min_cacheable_tokens("claude-sonnet-5") == 1024
    assert min_cacheable_tokens("claude-haiku-4-5") == 4096
    prefix = [_blocks(3000)]
    assert caches(prefix, model="claude-opus-5")
    assert not caches(prefix, model="claude-haiku-4-5")


def test_same_prefix_marked_on_opus_and_unmarked_on_haiku():
    prefix = [_blocks(3000)]
    assert "cache_control" in llm._cacheable(prefix, model="claude-opus-5")[-1]
    assert "cache_control" not in llm._cacheable(prefix, model="claude-haiku-4-5")[-1]


def test_never_exceeds_the_four_breakpoint_limit():
    client = FakeClient()
    write(client, "claude-opus-5", stable_blocks=[_blocks(600)] * 6,
          tail_blocks=[_blocks(600)] * 6, prompt="x")
    call = client.calls[0]
    explicit = sum(1 for b in call["system"] if "cache_control" in b)
    assert explicit + ("cache_control" in call) <= llm.MAX_CACHE_BREAKPOINTS


# ─────────────────────────────────────────────────── rates
@pytest.mark.parametrize("model,expected", [
    ("claude-opus-5", (5.0, 25.0)),
    ("claude-fable-5-1", (10.0, 50.0)),
    ("claude-sonnet-5", (2.0, 10.0)),
    ("claude-haiku-4-5", (1.0, 5.0)),
])
def test_rates_match_the_published_table(model, expected):
    assert rate_for(model) == expected


def test_a_stale_date_suffix_still_finds_its_rate():
    """The suffixed Haiku id used to miss the table and fall through to a
    default five times its real price."""
    assert rate_for("claude-haiku-4-5-20251001") == rate_for("claude-haiku-4-5")


def test_unknown_model_falls_back_high_so_the_budget_trips_early():
    assert rate_for("claude-something-unreleased") == llm.DEFAULT_RATE


def test_cached_input_is_billed_at_a_tenth():
    usage = Usage()
    usage.add("claude-opus-5", SimpleNamespace(
        input_tokens=0, output_tokens=0,
        cache_read_input_tokens=1_000_000, cache_creation_input_tokens=0))
    assert usage.usd == pytest.approx(5.0 * 0.1)


def test_fable_reads_at_a_fortieth_not_a_tenth():
    usage = Usage()
    usage.add("claude-fable-5-1", SimpleNamespace(
        input_tokens=0, output_tokens=0,
        cache_read_input_tokens=1_000_000, cache_creation_input_tokens=0))
    assert usage.usd == pytest.approx(0.25)


def test_cache_write_carries_its_premium():
    usage = Usage()
    usage.add("claude-opus-5", SimpleNamespace(
        input_tokens=0, output_tokens=0,
        cache_read_input_tokens=0, cache_creation_input_tokens=1_000_000))
    assert usage.usd == pytest.approx(5.0 * 1.25)


def test_batch_halves_the_bill():
    plain, batched = Usage(), Usage()
    u = SimpleNamespace(input_tokens=1_000_000, output_tokens=0,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    plain.add("claude-sonnet-5", u)
    batched.add("claude-sonnet-5", u, batch=True)
    assert batched.usd == pytest.approx(plain.usd / 2)


# ─────────────────────────────────────────── nothing marked that cannot cache
def test_extraction_sends_no_breakpoint_it_cannot_use(monkeypatch, world):
    """Checked on the request payload, not the source: extraction's system block
    is ~175 tokens, under every model's minimum, so marking it would cache
    nothing and spend one of the four slots."""
    from bp import extract
    from bp.extract import SYSTEM, ExtractReport
    from bp.policy import RunPolicy
    from bp.textstats import estimate_tokens

    graph, profile = world
    assert estimate_tokens(SYSTEM) < min_cacheable_tokens("claude-sonnet-5")

    captured: list[list[dict]] = []
    monkeypatch.setattr(extract, "submit_batch",
                        lambda client, requests: captured.append(requests) or "batch_1")
    monkeypatch.setattr(extract, "poll_batch", lambda client, batch_id: [])

    extract._run_batch(graph, profile, object(), "claude-sonnet-5", "events",
                       extract._Events, graph.scenes()[:2], ExtractReport(), lambda _: None)

    assert captured, "no batch was submitted"
    for request in captured[0]:
        for block in request["params"]["system"]:
            assert "cache_control" not in block

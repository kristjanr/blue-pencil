"""structured(): the tool-use fallback ladder, against a fake client.

The bug this file exists for: a strict schema is not the only way a structured
call comes back empty. Under `tool_choice: auto` the model is also free to
just answer in prose — seen on real adjudication calls where the honest
response to an ambiguous group is a clarifying question. Swapping the schema's
strictness does nothing about that, because the schema was never the problem;
these tests pin the further fallback (forcing the tool call) and the error
message a caller sees when nothing works.
"""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from bp import llm
from bp.errors import NoStructuredOutput, Refused
from bp.llm import structured


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    """`_retry` backs off for real between attempts; a non-retryable-named
    exception (BadRequestError and friends) skips that, but anything else,
    including this file's simulated 400s, would otherwise cost several real
    seconds per test."""
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)


class Widget(BaseModel):
    name: str = ""
    count: int = 0


def _usage():
    return SimpleNamespace(input_tokens=10, output_tokens=5,
                           cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _tool_use(payload: dict):
    return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=payload)],
                           stop_reason="tool_use", usage=_usage())


def _text_only(text: str = "I need more context before I can answer."):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
                           stop_reason="end_turn", usage=_usage())


def _refusal():
    return SimpleNamespace(content=[], stop_reason="refusal", usage=_usage())


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeMessages ran out of scripted responses")
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def test_happy_path_first_try():
    client = FakeClient([_tool_use({"name": "a", "count": 1})])
    result = structured(client, "claude-sonnet-5", Widget, prompt="describe it")
    assert result == Widget(name="a", count=1)
    assert client.messages.calls[0]["tool_choice"] == {"type": "auto"}


def test_falls_back_to_plain_schema_on_a_strict_schema_error():
    class BadRequestError(Exception):
        """Named to match what `_retry` treats as non-retryable, so the
        failure reaches structured()'s own fallback on the first try instead
        of being silently retried away by `_retry` itself."""

    class RaisingMessages(FakeMessages):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise BadRequestError("strict schema violation: unsupported keyword")
            return self._responses.pop(0)

    client = FakeClient([])
    client.messages = RaisingMessages([_tool_use({"name": "a", "count": 1})])
    result = structured(client, "claude-sonnet-5", Widget, prompt="describe it")
    assert result == Widget(name="a", count=1)
    assert len(client.messages.calls) == 2
    assert "strict" not in client.messages.calls[1]["tools"][0]


def test_falls_back_to_plain_schema_when_the_model_declines_silently():
    client = FakeClient([_text_only(), _tool_use({"name": "a", "count": 2})])
    result = structured(client, "claude-sonnet-5", Widget, prompt="describe it")
    assert result == Widget(name="a", count=2)
    assert len(client.messages.calls) == 2
    assert client.messages.calls[1]["tool_choice"] == {"type": "auto"}


def test_forces_the_tool_call_when_auto_keeps_getting_declined():
    """switching schema strictness alone was not the fix: the model was
    choosing, under `tool_choice: auto`, not to call any tool at all. Forcing
    the call — the same shape extraction's batch path already relies on — is
    the fallback structured() was missing."""
    client = FakeClient([_text_only(), _text_only(), _tool_use({"name": "a", "count": 3})])
    result = structured(client, "claude-sonnet-5", Widget, prompt="describe it")
    assert result == Widget(name="a", count=3)
    assert len(client.messages.calls) == 3
    assert client.messages.calls[2]["tool_choice"] == {"type": "tool", "name": "emit"}


def test_raises_an_informative_error_when_the_model_never_emits():
    client = FakeClient([_text_only("still not sure"), _text_only("still not sure"),
                         _text_only("still not sure")])
    with pytest.raises(NoStructuredOutput, match="still not sure"):
        structured(client, "claude-sonnet-5", Widget, prompt="describe it")


def test_a_model_that_rejects_a_forced_choice_still_surfaces_the_original_decline():
    """If the forced retry itself 400s (some models reject it outright), the
    caller should learn about the decline that triggered it, not a fresh,
    less informative crash from the forced attempt."""

    class RejectsForce(FakeMessages):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("tool_choice", {}).get("type") == "tool":
                raise Exception("400: forced tool_choice not supported")
            return self._responses.pop(0)

    client = FakeClient([])
    client.messages = RejectsForce([_text_only("need the chapter"), _text_only("need the chapter")])
    with pytest.raises(NoStructuredOutput, match="need the chapter"):
        structured(client, "claude-sonnet-5", Widget, prompt="describe it")


def test_refusal_raises_even_after_a_decline_fallback():
    client = FakeClient([_text_only(), _refusal()])
    with pytest.raises(Refused):
        structured(client, "claude-sonnet-5", Widget, prompt="describe it")

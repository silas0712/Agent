"""LLM client tests (all offline)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import pytest

from agentteam.llm import (
    BaseLLMClient,
    EchoLLM,
    LLMError,
    OpenAICompatibleClient,
    ScriptedLLM,
    extract_json,
    strip_code_fences,
)


def test_strip_code_fences_and_extract_json() -> None:
    assert strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    assert extract_json('Sure! Here is the plan:\n{"goal": "g", "steps": []}\nHope it helps.')["goal"] == "g"
    assert extract_json('[1, 2, 3]') == [1, 2, 3]
    assert extract_json('prefix {"text": "a } brace"} suffix') == {"text": "a } brace"}


def test_extract_json_rejects_garbage() -> None:
    with pytest.raises(LLMError):
        extract_json("no json at all")


def test_scripted_llm_replays_scripts_and_callables(run: Callable[[Any], Any]) -> None:
    scripted = ScriptedLLM([{"approved": True}, lambda messages: {"goal": messages[-1]["content"]}])

    async def scenario() -> tuple[Any, Any, Any]:
        first = await scripted.complete_json("ignored")
        second = await scripted.complete_json("写一个模块")
        third = await scripted.complete_json("again")  # repeat_last
        return first, second, third

    first, second, third = run(scenario())
    assert first == {"approved": True}
    assert second == {"goal": "写一个模块"}
    assert third == {"goal": "again"}  # repeating an entry re-evaluates callables
    assert scripted.calls == 3


def test_scripted_llm_validates_input() -> None:
    with pytest.raises(ValueError):
        ScriptedLLM([])


def test_complete_json_retries_after_invalid_output(run: Callable[[Any], Any]) -> None:
    client = ScriptedLLM(["I cannot help with that", {"ok": True}])

    assert run(client.complete_json("hi")) == {"ok": True}
    assert client.calls == 2


def test_complete_json_eventually_raises(run: Callable[[Any], Any]) -> None:
    with pytest.raises(LLMError):
        run(ScriptedLLM(["never json"]).complete_json("hi", retries=1))


def test_echo_llm_returns_the_prompt(run: Callable[[Any], Any]) -> None:
    payload = run(EchoLLM().complete_json("ping"))

    assert payload == {"echo": "ping"}


def test_openai_client_requires_an_api_key() -> None:
    with pytest.raises(LLMError, match="API key"):
        OpenAICompatibleClient("gpt-4o-mini", base_url="http://localhost:11434/v1")


def test_openai_payload_shape() -> None:
    client = OpenAICompatibleClient("qwen2.5-coder", base_url="http://localhost:11434/v1/", api_key="k", max_tokens=64)
    payload = client._payload([{"role": "user", "content": "hi"}], "be brief", None, None)

    assert client.base_url == "http://localhost:11434/v1"
    assert payload["model"] == "qwen2.5-coder"
    assert payload["max_tokens"] == 64
    assert payload["messages"][0] == {"role": "system", "content": "be brief"}
    assert payload["messages"][1] == {"role": "user", "content": "hi"}


def test_base_client_is_abstract() -> None:
    with pytest.raises(TypeError):
        BaseLLMClient()  # type: ignore[abstract]


def test_custom_client_subclass_works(run: Callable[[Any], Any]) -> None:
    class UpperLLM(BaseLLMClient):
        provider = "upper"

        async def chat(
            self,
            messages: Sequence[dict[str, str]],
            *,
            system: str | None = None,
            temperature: float | None = None,
            max_tokens: int | None = None,
        ):
            from agentteam.llm import LLMResponse

            return LLMResponse(text='{"echo": "PING"}', provider=self.provider, model=self.model)

    assert run(UpperLLM("upper-1").complete_json("ping")) == {"echo": "PING"}

"""Uniform async interface every agent uses to talk to a model.

Implementations:

* :class:`OpenAICompatibleClient` - any OpenAI-compatible HTTP endpoint
  (OpenAI, Azure, DeepSeek, vLLM, Ollama, LM Studio, ...).
* :class:`ScriptedLLM` / :class:`EchoLLM` - deterministic offline clients so
  the whole multi-agent workflow can run and be tested without an API key.
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
_JSON_REPAIR_HINT = (
    "Your previous answer was not valid JSON. Reply with ONE valid JSON object only, "
    "no markdown fences and no explanations."
)
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+\-]*\s*(.*?)```", re.DOTALL)


class LLMError(RuntimeError):
    """Raised when a model call fails or returns something unusable."""


@dataclass
class LLMResponse:
    text: str
    provider: str = "unknown"
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)


def strip_code_fences(text: str) -> str:
    """Return the content of the first markdown code fence, if any."""

    match = _FENCE_RE.search(text or "")
    return match.group(1).strip() if match else (text or "").strip()


def _first_balanced_block(text: str) -> str:
    """Slice out the first balanced ``{...}`` / ``[...]`` region of *text*."""

    start = -1
    opener = ""
    for index, char in enumerate(text):
        if char in "{[":
            start, opener = index, char
            break
    if start < 0:
        return ""

    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return text[start:]


def extract_json(text: str) -> Any:
    """Best-effort JSON parsing of a model answer (tolerates fences/prose)."""

    candidates: list[str] = []
    stripped = strip_code_fences(text)
    if stripped:
        candidates.append(stripped)
    candidates.extend(match.group(1).strip() for match in _FENCE_RE.finditer(text or ""))
    balanced = _first_balanced_block(text or "")
    if balanced:
        candidates.append(balanced)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise LLMError(f"model did not return JSON: {(text or '')[:400]!r}")


class BaseLLMClient(abc.ABC):
    """Stateful, async, reusable client for one model."""

    provider = "base"

    def __init__(self, model: str = "", *, temperature: float = 0.2, max_tokens: int | None = None) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    @abc.abstractmethod
    async def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Send ``messages`` (``[{"role": ..., "content": ...}]``) to the model."""

    async def complete_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        retries: int = 1,
        temperature: float | None = None,
    ) -> Any:
        """Ask the model a question and parse its JSON answer."""

        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        last_error: LLMError | None = None
        for _ in range(max(1, retries + 1)):
            response = await self.chat(messages, system=system, temperature=temperature)
            try:
                return extract_json(response.text)
            except LLMError as exc:
                last_error = exc
                logger.debug("%s returned non-JSON output, asking again", self.provider)
                messages = [
                    *messages,
                    {"role": "assistant", "content": response.text},
                    {"role": "user", "content": _JSON_REPAIR_HINT},
                ]
        raise last_error or LLMError("empty response")

    async def aclose(self) -> None:
        return None


class ScriptedLLM(BaseLLMClient):
    """Deterministic offline client replaying a fixed script.

    Each entry may be a string, a JSON-serialisable object or a callable that
    receives the messages and returns either. Callables make the offline demo
    feel alive (the planner echoes the real goal, the actor writes real files).
    """

    provider = "scripted"

    def __init__(
        self,
        responses: Iterable[Any],
        *,
        model: str = "scripted",
        repeat_last: bool = True,
    ) -> None:
        super().__init__(model=model)
        self._responses: list[Any] = list(responses)
        if not self._responses:
            raise ValueError("ScriptedLLM needs at least one response")
        self.repeat_last = repeat_last
        self.calls = 0

    @property
    def exhausted(self) -> bool:
        return self.calls >= len(self._responses)

    def reset(self) -> None:
        self.calls = 0

    async def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        if self.calls >= len(self._responses):
            if not self.repeat_last:
                raise LLMError(f"scripted response list exhausted after {self.calls} calls")
            index = len(self._responses) - 1
        else:
            index = self.calls
        self.calls += 1

        item = self._responses[index]
        if callable(item):
            item = item(messages)
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return LLMResponse(text=text, provider=self.provider, model=self.model)


class EchoLLM(BaseLLMClient):
    """Trivial client: echoes the last user message as ``{"echo": ...}``."""

    provider = "echo"

    async def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        last = messages[-1]["content"] if messages else ""
        return LLMResponse(text=json.dumps({"echo": last}, ensure_ascii=False), provider=self.provider, model=self.model)


class OpenAICompatibleClient(BaseLLMClient):
    """Talks to any OpenAI-compatible ``/chat/completions`` endpoint.

    Uses the official ``openai`` SDK when installed and falls back to a tiny
    ``urllib`` implementation otherwise, so no hard dependency is required.
    """

    provider = "openai"

    def __init__(
        self,
        model: str,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(model=model, temperature=temperature, max_tokens=max_tokens)
        if not api_key:
            raise LLMError(
                f"provider 'openai' needs an API key for model {model!r} "
                "(set OPENAI_API_KEY or AGENT_<ROLE>_API_KEY)"
            )
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client: Any = None
        self._client_ready = False

    # -- internals ---------------------------------------------------------
    def _payload(
        self,
        messages: Sequence[dict[str, str]],
        system: str | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        history: list[dict[str, str]] = []
        if system:
            history.append({"role": "system", "content": system})
        history.extend({"role": m["role"], "content": m["content"]} for m in messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": history,
            "temperature": self.temperature if temperature is None else temperature,
        }
        limit = self.max_tokens if max_tokens is None else max_tokens
        if limit:
            payload["max_tokens"] = limit
        return payload

    def _sdk_client(self) -> Any:
        if self._client_ready:
            return self._client
        self._client_ready = True
        try:
            from openai import AsyncOpenAI  # type: ignore[import-not-found]
        except ImportError:
            return None
        self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)
        return self._client

    # -- public API --------------------------------------------------------
    async def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        payload = self._payload(messages, system, temperature, max_tokens)
        client = self._sdk_client()
        if client is not None:
            return await self._chat_with_sdk(client, payload)
        return await asyncio.to_thread(self._chat_with_urllib, payload)

    async def _chat_with_sdk(self, client: Any, payload: dict[str, Any]) -> LLMResponse:
        try:
            completion = await client.chat.completions.create(**payload)
        except Exception as exc:  # pragma: no cover - depends on the endpoint
            raise LLMError(f"{self.provider} call failed: {exc}") from exc
        try:
            text = completion.choices[0].message.content or ""
        except (AttributeError, IndexError) as exc:
            raise LLMError(f"unexpected completion payload: {completion!r}") from exc
        usage: dict[str, int] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(getattr(completion, "usage", None), key, None)
            if isinstance(value, int):
                usage[key] = value
        return LLMResponse(text=text, provider=self.provider, model=self.model, usage=usage)

    def _chat_with_urllib(self, payload: dict[str, Any]) -> LLMResponse:  # pragma: no cover - network
        url = self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise LLMError(f"HTTP {exc.code} from {url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"cannot reach {url}: {exc.reason}") from exc

        try:
            text = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response body: {str(body)[:400]}") from exc
        usage = {k: v for k, v in (body.get("usage") or {}).items() if isinstance(v, int)}
        return LLMResponse(text=text, provider=self.provider, model=self.model, usage=usage)

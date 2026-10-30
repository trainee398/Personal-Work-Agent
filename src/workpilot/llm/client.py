"""One model step as plain data, with a deterministic offline implementation."""

import inspect
import json
from collections.abc import Awaitable, Callable, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str = field(repr=False)


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = field(default="", repr=False)
    tool_calls: tuple[ToolCall, ...] = ()
    call_id: str | None = None
    # JSON-only provider continuation (e.g. encrypted reasoning), never printed or persisted.
    continuation: tuple[dict[str, Any], ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[Message, ...] = field(repr=False)
    tools: tuple[ToolDefinition, ...]
    timeout_s: float
    max_output_tokens: int

    @property
    def context_chars(self) -> int:
        return len(
            json_text(
                {
                    "messages": [asdict(m) for m in self.messages],
                    "tools": [asdict(t) for t in self.tools],
                }
            )
        )


@dataclass(frozen=True)
class ModelResponse:
    text: str = field(default="", repr=False)
    tool_calls: tuple[ToolCall, ...] = ()
    tokens_in: int = 0
    tokens_out: int = 0
    continuation: tuple[dict[str, Any], ...] = field(default=(), repr=False)


class ModelClient(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    async def aclose(self) -> None: ...


StubStep = (
    ModelResponse | Exception | Callable[[ModelRequest], ModelResponse | Awaitable[ModelResponse]]
)


class StubModel:
    """Scripted tool calls/final answers; no SDK, credentials, network or hidden fallback."""

    def __init__(self, script: Sequence[StubStep]) -> None:
        self._steps = iter(script)
        self.requests: list[ModelRequest] = []
        self.closed = False

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(deepcopy(request))
        try:
            step = next(self._steps)
        except StopIteration:
            raise RuntimeError("Offline model script exhausted.") from None
        if isinstance(step, Exception):
            raise step
        result = step(request) if callable(step) else step
        return await result if inspect.isawaitable(result) else result

    async def aclose(self) -> None:
        self.closed = True

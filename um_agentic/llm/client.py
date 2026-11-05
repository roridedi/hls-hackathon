import asyncio
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Protocol

from pydantic import Field, JsonValue

from um_agentic.contracts import Contract
from um_agentic.serialization import canonical_json


class ModelRequest(Contract):
    task: str
    system_prompt: str
    prompt_version: str
    inputs: dict[str, JsonValue]
    response_schema: dict[str, JsonValue]
    max_tokens: int = Field(gt=0)
    repair_error: str | None = None


class ModelResponse(Contract):
    content: str
    model_id: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class LLMClient(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse: ...


class FakeLLM:
    model_id = "deterministic-fake-v1"

    def __init__(
        self,
        handlers: dict[str, Callable[[dict[str, JsonValue]], dict[str, JsonValue]]] | None = None,
        scripted: dict[str, list[str]] | None = None,
    ) -> None:
        self.handlers = handlers or {}
        self.scripted = defaultdict[str, deque[str]](deque)
        for task, outputs in (scripted or {}).items():
            self.scripted[task].extend(outputs)
        self.calls: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        await asyncio.sleep(0)
        self.calls.append(request)
        if self.scripted[request.task]:
            content = self.scripted[request.task].popleft()
        elif request.task in self.handlers:
            content = canonical_json(self.handlers[request.task](request.inputs))
        else:
            raise ValueError(f"No deterministic handler is registered for {request.task}.")
        return ModelResponse(
            content=content, model_id=self.model_id,
            input_tokens=max(1, len(canonical_json(request.inputs)) // 4),
            output_tokens=max(1, len(content) // 4),
        )

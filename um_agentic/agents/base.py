import asyncio
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from opentelemetry import trace
from pydantic import JsonValue, ValidationError

from um_agentic.contracts import AgentRun, Contract
from um_agentic.errors import AgentFailure
from um_agentic.llm.client import LLMClient, ModelRequest
from um_agentic.llm.prompts import load_prompt
from um_agentic.security.telemetry import record_agent_metrics
from um_agentic.serialization import canonical_json, content_hash
from um_agentic.settings import Configuration


class ReadTool(StrEnum):
    PARSERS = "parsers"
    DOCUMENT_EXTRACTOR = "document_extractor"
    ELIGIBILITY = "eligibility_read"
    PLAN_RULES = "plan_rules_read"
    CRITERIA = "criteria_read"
    EVIDENCE = "evidence_read"
    FHIR = "fhir_read"
    CLAIMS = "claims_read"
    SOURCES = "sources_read"
    POLICY = "policy_read"
    CASE = "case_read"
    TEMPLATES = "templates_read"
    ANALYTICS = "analytics_read"


@dataclass(frozen=True)
class AgentSpec[OutputT: Contract]:
    name: str
    output_type: type[OutputT]
    allowed_tools: tuple[ReadTool, ...]


@dataclass(frozen=True)
class AgentResult[OutputT: Contract]:
    output: OutputT | None
    run: AgentRun


class AgentRunner:
    def __init__(self, client: LLMClient, config: Configuration) -> None:
        self.client = client
        self.config = config

    async def run[OutputT: Contract](
        self, spec: AgentSpec[OutputT], case_id: UUID | None,
        inputs: dict[str, JsonValue], criteria_version: str | None = None,
        prompt_override: tuple[str, str, str] | None = None,
    ) -> AgentResult[OutputT]:
        with trace.get_tracer("um_agentic").start_as_current_span(
            "um.agent.run",
            attributes={"um.agent": spec.name, "um.criteria_version": criteria_version or "none"},
            record_exception=False, set_status_on_exception=False,
        ) as span:
            result = await self._run(spec, case_id, inputs, criteria_version, prompt_override)
            run = result.run
            attributes: dict[str, str | int | float | bool] = {
                "um.agent": spec.name, "um.model_id": run.model_id,
                "um.prompt_version": run.prompt_version, "um.latency_ms": run.latency_ms,
                "um.input_tokens": run.input_tokens, "um.output_tokens": run.output_tokens,
                "um.inputs_hash": run.inputs_hash,
            }
            if case_id is not None:
                attributes["um.case_id"] = str(case_id)
            span.set_attributes(attributes)
            logging.getLogger("um_agentic").info(
                "agent_failed" if run.error else "agent_completed",
                extra={"safe_attributes": attributes},
            )
            record_agent_metrics(spec.name, run.model_id, run.latency_ms, run.input_tokens, run.output_tokens)
            return result

    async def _run[OutputT: Contract](
        self,
        spec: AgentSpec[OutputT],
        case_id: UUID | None,
        inputs: dict[str, JsonValue],
        criteria_version: str | None = None,
        prompt_override: tuple[str, str, str] | None = None,
    ) -> AgentResult[OutputT]:
        version, prompt, prompt_hash = prompt_override or load_prompt(spec.name)
        if content_hash(prompt) != prompt_hash:
            raise AgentFailure("Prompt integrity check failed.")
        budget = self.config.budget(spec.name)
        started = time.monotonic()
        input_tokens = output_tokens = 0
        error: str | None = None
        model_id = "unavailable"
        output: OutputT | None = None
        attempts = 0
        raw_outputs: list[str] = []
        responses: list[dict[str, JsonValue]] = []
        for attempt in range(2):
            attempts = attempt + 1
            remaining = budget.timeout_seconds - (time.monotonic() - started)
            remaining_tokens = budget.token_budget - input_tokens - output_tokens
            if remaining <= 0 or remaining_tokens <= 0:
                error = "Agent budget exhausted."
                break
            request = ModelRequest(
                task=spec.name, system_prompt=prompt, prompt_version=version,
                inputs=inputs, response_schema=spec.output_type.model_json_schema(),
                max_tokens=remaining_tokens, repair_error=error,
            )
            try:
                response = await asyncio.wait_for(
                    self.client.complete(request), timeout=remaining
                )
                model_id = response.model_id
                raw_outputs.append(response.content)
                responses.append(response.model_dump(mode="json"))
                input_tokens += response.input_tokens
                output_tokens += response.output_tokens
                if input_tokens + output_tokens > budget.token_budget:
                    raise AgentFailure("Agent token budget exceeded.")
                output = spec.output_type.model_validate_json(response.content)
                error = None
                break
            except ValidationError:
                error = "Output did not match the required schema. Return only that schema."
            except TimeoutError:
                error = "Agent timed out. Human review is required."
                break
            except (AgentFailure, ConnectionError, OSError, ValueError, RuntimeError) as exc:
                error = (
                    str(exc) if isinstance(exc, AgentFailure)
                    else f"Agent execution failed ({type(exc).__name__}). Human review is required."
                )
                break
        serialized = output.model_dump(mode="json") if output is not None else None
        run = AgentRun(
            case_id=case_id, agent=spec.name, model_id=model_id,
            prompt_version=version, prompt_hash=prompt_hash,
            prompt_text=prompt,
            criteria_version=criteria_version,
            inputs_hash=content_hash(inputs), inputs=inputs,
            outputs=serialized,
            outputs_hash=content_hash(serialized) if serialized is not None else None,
            raw_outputs=tuple(raw_outputs),
            responses=tuple(responses),
            latency_ms=(time.monotonic() - started) * 1000,
            input_tokens=input_tokens, output_tokens=output_tokens,
            attempts=attempts, error=error,
        )
        return AgentResult(output=output, run=run)


def estimate_tokens(value: object) -> int:
    return max(1, len(canonical_json(value)) // 4)

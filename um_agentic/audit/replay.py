import asyncio
from collections import deque
from uuid import UUID

from pydantic import JsonValue

from um_agentic.adapters.intake import IntakeEnvelope, normalize
from um_agentic.agents.base import AgentRunner
from um_agentic.agents.fakes import fake_handlers
from um_agentic.agents.recommendation.runner import recommend
from um_agentic.agents.registry import SPECS
from um_agentic.contracts import (
    AgentRun,
    Contract,
    Contradiction,
    CoverageResult,
    CriteriaEvaluation,
    CriteriaSet,
    DocumentationOutput,
    EvidenceItem,
)
from um_agentic.errors import AgentFailure, Conflict
from um_agentic.llm import FakeLLM, LLMClient, ModelRequest, ModelResponse
from um_agentic.persistence import Store
from um_agentic.serialization import content_hash
from um_agentic.settings import Configuration


class ReplayResult(Contract):
    case_id: UUID
    runs_reproduced: int
    outputs_match: bool
    recorded_model_responses: int
    deterministic_recomputations: int


class RecordedLLM:
    def __init__(self, run: AgentRun) -> None:
        self.run = run
        self.responses = deque(run.responses)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if (
            request.task != self.run.agent
            or content_hash(request.inputs) != self.run.inputs_hash
            or content_hash(request.system_prompt) != self.run.prompt_hash
            or request.prompt_version != self.run.prompt_version
        ):
            raise Conflict("Replay inputs or prompt version differ from the stored run.")
        if not self.responses:
            raise AgentFailure(self.run.error or "Recorded run has no model response.")
        return ModelResponse.model_validate(self.responses.popleft())


def _models[ModelT: Contract](raw: JsonValue, model: type[ModelT]) -> tuple[ModelT, ...]:
    if not isinstance(raw, list):
        raise Conflict("Replay inputs have an invalid collection.")
    return tuple(model.model_validate(item) for item in raw)


async def _replay_run(run: AgentRun, config: Configuration, original: IntakeEnvelope) -> dict | None:
    if run.prompt_hash != content_hash(run.prompt_text) or run.inputs_hash != content_hash(run.inputs):
        raise Conflict("Replay input or prompt integrity failed.")
    if run.outputs_hash != (content_hash(run.outputs) if run.outputs is not None else None):
        raise Conflict("Replay output integrity failed.")
    if run.agent == "intake_normalizer":
        request = original.model_copy(update={"payload": run.inputs["payload"]})
        return normalize(request).request.model_dump(mode="json")
    if run.agent == "coverage_check":
        return CoverageResult.model_validate(run.outputs).model_dump(mode="json")
    if run.agent == "recommendation":
        inputs = run.inputs
        selected = config.model_copy(update={
            "extraction_score_threshold": inputs["extraction_score_threshold"],
        })
        return recommend(
            CriteriaSet.model_validate(inputs["criteria"]),
            CriteriaEvaluation.model_validate(inputs["evaluation"]),
            _models(inputs["evidence"], EvidenceItem),
            _models(inputs["contradictions"], Contradiction),
            DocumentationOutput.model_validate(inputs["documentation"]),
            selected,
        ).model_dump(mode="json")
    spec = SPECS.get(run.agent)
    if spec is None:
        raise Conflict("This implementation cannot replay the stored agent version.")
    client: LLMClient
    if run.model_id == FakeLLM.model_id and run.error is None:
        client = FakeLLM(handlers=fake_handlers())
    else:
        client = RecordedLLM(run)
    result = await AgentRunner(client, config).run(
        spec, run.case_id, run.inputs, run.criteria_version,
        prompt_override=(run.prompt_version, run.prompt_text, run.prompt_hash),
    )
    return result.output.model_dump(mode="json") if result.output else None


def replay_case(store: Store, case_id: UUID, config: Configuration) -> ReplayResult:
    if not store.verify_chain(case_id):
        raise Conflict("Case event chain or aggregate integrity failed.")
    case = store.get(case_id)
    original = IntakeEnvelope.model_validate(case.original_request)
    runs = store.runs(case_id)
    deterministic = 0
    recorded = 0
    for run in runs:
        outputs = asyncio.run(_replay_run(run, config, original))
        if outputs != run.outputs:
            raise Conflict(f"Replay differs for agent {run.agent} at prompt version {run.prompt_version}.")
        deterministic += run.model_id == "deterministic-rules-v1"
        recorded += run.model_id not in {"deterministic-rules-v1", FakeLLM.model_id}
    return ReplayResult(
        case_id=case_id, runs_reproduced=len(runs), outputs_match=True,
        recorded_model_responses=recorded, deterministic_recomputations=deterministic,
    )

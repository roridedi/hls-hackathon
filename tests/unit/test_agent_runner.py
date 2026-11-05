import asyncio

from um_agentic.agents.base import AgentRunner
from um_agentic.agents.clinical_evidence.runner import SPEC
from um_agentic.contracts import CaseAggregate
from um_agentic.llm import FakeLLM, ModelRequest, ModelResponse
from um_agentic.settings import Budget, Configuration


async def test_schema_repair_occurs_once(
    config: Configuration, aggregate: CaseAggregate
) -> None:
    fake = FakeLLM(scripted={"clinical_evidence": ['{"outcome":"DENIED"}', '{"evidence":[]}']})
    result = await AgentRunner(fake, config).run(SPEC, aggregate.case.case_id, {})
    assert result.output is not None
    assert result.run.attempts == 2
    assert fake.calls[1].repair_error is not None


async def test_second_schema_failure_has_no_success_output(
    config: Configuration, aggregate: CaseAggregate
) -> None:
    fake = FakeLLM(scripted={"clinical_evidence": ['{"outcome":"DENIED"}'] * 2})
    result = await AgentRunner(fake, config).run(SPEC, aggregate.case.case_id, {})
    assert result.output is None and result.run.error
    assert result.run.attempts == 2


async def test_timeout_and_token_budget(
    config: Configuration, aggregate: CaseAggregate
) -> None:
    class SlowLLM:
        async def complete(self, request: ModelRequest) -> ModelResponse:
            await asyncio.sleep(0.1)
            return ModelResponse(content='{"evidence":[]}', model_id="slow", input_tokens=1, output_tokens=1)

    limited = config.model_copy(update={
        "agent_budgets": {"clinical_evidence": Budget(timeout_seconds=0.01, token_budget=10)},
    })
    result = await AgentRunner(SlowLLM(), limited).run(SPEC, aggregate.case.case_id, {})
    assert result.output is None and "timed out" in (result.run.error or "")
    fake = FakeLLM(scripted={"clinical_evidence": ['{"evidence":[]}']})
    tiny = limited.model_copy(update={
        "agent_budgets": {"clinical_evidence": Budget(timeout_seconds=1, token_budget=1)},
    })
    result = await AgentRunner(fake, tiny).run(SPEC, aggregate.case.case_id, {})
    assert result.output is None and "budget" in (result.run.error or "")

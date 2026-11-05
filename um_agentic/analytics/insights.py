from datetime import date

from um_agentic.agents.insights.runner import SPEC, summarize
from um_agentic.analytics.metrics import Analytics
from um_agentic.contracts import Contract, InsightsOutput
from um_agentic.errors import AgentFailure
from um_agentic.service import UMService


class ProgramInsights(Contract):
    report_id: str
    summary: InsightsOutput
    advisory_only: bool = True


async def weekly_insights(
    service: UMService, start: date | None = None, end: date | None = None,
) -> ProgramInsights:
    report = Analytics(service.store, service.config).report(start, end)
    inputs = report.model_dump(mode="json")
    result = await service.runner.run(SPEC, None, inputs)
    service.store.save_program_run(result.run)
    if result.output is None:
        raise AgentFailure(result.run.error or "Program insights failed.")
    return ProgramInsights(report_id=str(result.run.run_id), summary=summarize(inputs))

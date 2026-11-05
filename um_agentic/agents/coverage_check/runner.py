from um_agentic.agents.base import AgentSpec, ReadTool
from um_agentic.agents.coverage_check.schema import Output

SPEC = AgentSpec("coverage_check", Output, (ReadTool.ELIGIBILITY, ReadTool.PLAN_RULES))

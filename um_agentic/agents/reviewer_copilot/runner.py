from um_agentic.agents.base import AgentSpec, ReadTool
from um_agentic.agents.reviewer_copilot.schema import Output

SPEC = AgentSpec("reviewer_copilot", Output, (ReadTool.CASE, ReadTool.EVIDENCE))

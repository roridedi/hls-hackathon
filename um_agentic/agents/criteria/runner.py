from um_agentic.agents.base import AgentSpec, ReadTool
from um_agentic.agents.criteria.schema import Output

SPEC = AgentSpec("criteria", Output, (ReadTool.CRITERIA, ReadTool.POLICY, ReadTool.EVIDENCE))

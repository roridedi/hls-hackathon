from um_agentic.agents.base import AgentSpec, ReadTool
from um_agentic.agents.letter.schema import Output

SPEC = AgentSpec("letter", Output, (ReadTool.TEMPLATES,))

from um_agentic.agents.base import AgentSpec, ReadTool
from um_agentic.agents.intake_normalizer.schema import Output

SPEC = AgentSpec("intake_normalizer", Output, (ReadTool.PARSERS, ReadTool.DOCUMENT_EXTRACTOR))

from um_agentic.agents.base import AgentSpec, ReadTool
from um_agentic.agents.clinical_evidence.schema import Output

SPEC = AgentSpec("clinical_evidence", Output, (
    ReadTool.DOCUMENT_EXTRACTOR, ReadTool.FHIR, ReadTool.CLAIMS,
))

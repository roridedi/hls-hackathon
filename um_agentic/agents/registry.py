from um_agentic.agents.base import AgentSpec
from um_agentic.agents.clinical_evidence.runner import SPEC as CLINICAL_EVIDENCE
from um_agentic.agents.coverage_check.runner import SPEC as COVERAGE
from um_agentic.agents.criteria.runner import SPEC as CRITERIA
from um_agentic.agents.documentation.runner import SPEC as DOCUMENTATION
from um_agentic.agents.insights.runner import SPEC as INSIGHTS
from um_agentic.agents.intake_normalizer.runner import SPEC as INTAKE
from um_agentic.agents.letter.runner import SPEC as LETTER
from um_agentic.agents.recommendation.runner import SPEC as RECOMMENDATION
from um_agentic.agents.reviewer_copilot.runner import SPEC as COPILOT
from um_agentic.agents.verifier.runner import SPEC as VERIFIER
from um_agentic.contracts import Contract

SPECS: dict[str, AgentSpec[Contract]] = {
    spec.name: spec for spec in (
        INTAKE, COVERAGE, DOCUMENTATION, CLINICAL_EVIDENCE, CRITERIA,
        VERIFIER, RECOMMENDATION, COPILOT, LETTER, INSIGHTS,
    )
}

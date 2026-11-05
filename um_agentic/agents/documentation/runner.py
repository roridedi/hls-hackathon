from um_agentic.agents.base import AgentSpec, ReadTool
from um_agentic.agents.documentation.schema import Output
from um_agentic.contracts import CriteriaEvaluation, CriteriaSet, CriterionStatus, DTRQuestion

SPEC = AgentSpec("documentation", Output, (ReadTool.CRITERIA, ReadTool.EVIDENCE))


def questions_for(criteria: CriteriaSet, evaluation: CriteriaEvaluation) -> Output:
    unknown = {r.criterion_id for r in evaluation.results if r.status == CriterionStatus.UNKNOWN}
    return Output(questions=tuple(
        DTRQuestion(
            criterion_id=c.id, text=c.question or f"Please provide supporting records for: {c.description}",
        )
        for c in criteria.criteria if c.id in unknown and not c.requires_clinical_judgment
    ))

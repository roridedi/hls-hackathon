from um_agentic.agents.base import AgentSpec, ReadTool
from um_agentic.agents.recommendation.schema import Output
from um_agentic.contracts import (
    AgentOutcome,
    Confidence,
    ConfidenceComponents,
    Contradiction,
    CriteriaEvaluation,
    CriteriaSet,
    CriterionStatus,
    DocumentationOutput,
    EvidenceItem,
    SourceType,
)
from um_agentic.settings import Configuration

SPEC = AgentSpec("recommendation", Output, (ReadTool.EVIDENCE, ReadTool.CRITERIA))


def recommend(
    criteria: CriteriaSet, evaluation: CriteriaEvaluation,
    evidence: tuple[EvidenceItem, ...], contradictions: tuple[Contradiction, ...],
    documentation: DocumentationOutput, config: Configuration,
) -> Output:
    ids = {identity for result in evaluation.results for identity in result.evidence_ids}
    used = tuple(item for item in evidence if item.verified and item.evidence_id in ids)
    all_met = evaluation.overall == "ALL_MET"
    judgment = any(c.requires_clinical_judgment for c in criteria.criteria)
    documents = {e.citation.source_id for e in used if e.citation.source_type == SourceType.DOCUMENT}
    only_one_document = bool(used) and len(documents) == 1 and all(
        item.citation.source_type == SourceType.DOCUMENT for item in used
    )
    minimum_score = min((item.extraction_score for item in used), default=0.0)
    if not all_met or contradictions or judgment:
        confidence = Confidence.LOW
        explanation = "Gaps, uncertainty, contradictions, or clinical judgment require human review."
    elif only_one_document or minimum_score < config.extraction_score_threshold:
        confidence = Confidence.MEDIUM
        explanation = "All criteria are met, but source diversity or extraction confidence is limited."
    else:
        confidence = Confidence.HIGH
        explanation = "All criteria have verified evidence with no contradictions."
    if contradictions or judgment or any(r.status == CriterionStatus.NOT_MET for r in evaluation.results):
        outcome = AgentOutcome.PEND_CLINICAL_REVIEW
    elif not all_met:
        outcome = AgentOutcome.PEND_MISSING_INFO
    else:
        outcome = AgentOutcome.APPROVE_RECOMMENDED
    reasons = tuple(f"{r.criterion_id}: {r.rationale}" for r in evaluation.results)
    if contradictions:
        reasons = (*reasons, "Conflicting source facts require a licensed clinical review.")
    return Output(
        outcome=outcome, confidence=confidence,
        components=ConfidenceComponents(
            all_required_met=all_met, verified_evidence_count=len(used),
            contradiction_count=len(contradictions), unstructured_source_count=len(documents),
            min_extraction_score=minimum_score, clinical_judgment_required=judgment,
            explanation=explanation,
        ),
        reasons=reasons,
        open_questions=tuple(question.text for question in documentation.questions),
        routing_queue="physician" if contradictions or judgment else "nurse",
    )

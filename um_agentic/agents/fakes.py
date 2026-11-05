from collections.abc import Callable
from datetime import date

from pydantic import JsonValue

from um_agentic.adapters.sources import extract_sources, supports
from um_agentic.agents.documentation.runner import questions_for
from um_agentic.agents.insights.runner import summarize
from um_agentic.contracts import (
    CriteriaMappingOutput,
    CriteriaSet,
    CriterionResult,
    CriterionStatus,
    EvidenceItem,
    ExtractionOutput,
    LetterDraftOutput,
    Source,
    SummaryOutput,
    VerificationCheck,
    VerificationOutput,
)
from um_agentic.rules import evaluate


def extraction(inputs: dict[str, JsonValue]) -> dict[str, JsonValue]:
    raw = inputs.get("sources")
    if not isinstance(raw, list):
        raise ValueError("Extraction requires source data.")
    sources = tuple(Source.model_validate(item) for item in raw)
    return ExtractionOutput(evidence=extract_sources(sources)).model_dump(mode="json")


def verification(inputs: dict[str, JsonValue]) -> dict[str, JsonValue]:
    raw_sources, raw_evidence = inputs.get("sources"), inputs.get("evidence")
    if not isinstance(raw_sources, list) or not isinstance(raw_evidence, list):
        raise ValueError("Verification requires evidence and source data.")
    sources = tuple(Source.model_validate(item) for item in raw_sources)
    evidence = tuple(EvidenceItem.model_validate(item) for item in raw_evidence)
    return VerificationOutput(checks=tuple(
        VerificationCheck(evidence_id=item.evidence_id, supported=result[0], note=result[1])
        for item in evidence for result in [supports(item, sources)]
    )).model_dump(mode="json")


def fake_handlers() -> dict[str, Callable[[dict[str, JsonValue]], dict[str, JsonValue]]]:
    return {
        "clinical_evidence": extraction, "verifier": verification,
        "documentation": documentation, "criteria": clinical_judgment,
        "letter": letter, "reviewer_copilot": reviewer_copilot,
        "insights": lambda inputs: summarize(inputs).model_dump(mode="json"),
    }


def documentation(inputs: dict[str, JsonValue]) -> dict[str, JsonValue]:
    criteria = CriteriaSet.model_validate(inputs["criteria"])
    raw = inputs["sources"]
    if not isinstance(raw, list):
        raise ValueError("Documentation requires sources.")
    sources = tuple(Source.model_validate(item) for item in raw)
    draft = tuple(item.model_copy(update={"verified": True}) for item in extract_sources(sources))
    as_of = date.fromisoformat(str(inputs["as_of"]))
    return questions_for(criteria, evaluate(criteria, draft, as_of)).model_dump(mode="json")


def clinical_judgment(inputs: dict[str, JsonValue]) -> dict[str, JsonValue]:
    criteria = CriteriaSet.model_validate(inputs["criteria"])
    return CriteriaMappingOutput(results=tuple(
        CriterionResult(
            criterion_id=criterion.id, status=CriterionStatus.UNKNOWN,
            rationale="Licensed clinical judgment is required.",
        )
        for criterion in criteria.criteria if criterion.requires_clinical_judgment
    )).model_dump(mode="json")


def letter(inputs: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return LetterDraftOutput(
        member_text=str(inputs["member_text"]), provider_text=str(inputs["provider_text"]),
    ).model_dump(mode="json")


def reviewer_copilot(inputs: dict[str, JsonValue]) -> dict[str, JsonValue]:
    raw = inputs.get("evidence")
    if not isinstance(raw, list):
        raise ValueError("Reviewer assistance requires evidence.")
    evidence = tuple(EvidenceItem.model_validate(item) for item in raw)
    question = str(inputs.get("question", "")).lower()
    terms = {
        "a1c": "lab_a1c", "bmi": "bmi", "metformin": "therapy_duration",
        "diagnosis": "diagnosis", "neurologic": "neurologic_deficit",
        "lifestyle": "lifestyle_program",
    }
    relevant = {fact_type for word, fact_type in terms.items() if word in question}
    ids = tuple(item.evidence_id for item in evidence if not relevant or item.fact_type in relevant)
    return SummaryOutput(summary="Selected verified source facts.", evidence_ids=ids).model_dump(mode="json")

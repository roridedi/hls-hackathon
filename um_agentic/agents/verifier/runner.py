from collections import defaultdict

from um_agentic.adapters.sources import supports
from um_agentic.agents.base import AgentSpec, ReadTool
from um_agentic.agents.verifier.schema import Output
from um_agentic.contracts import Contradiction, EvidenceItem, Source
from um_agentic.serialization import canonical_json

SPEC = AgentSpec("verifier", Output, (ReadTool.SOURCES,))


def contradictions_in(evidence: tuple[EvidenceItem, ...]) -> tuple[Contradiction, ...]:
    grouped: dict[tuple[str, str, str], list[EvidenceItem]] = defaultdict(list)
    for item in evidence:
        if not item.verified or item.fact_type == "diagnosis":
            continue
        target = ""
        if isinstance(item.normalized_value, dict):
            target = str(item.normalized_value.get("drug_class", item.normalized_value.get("target", "")))
        grouped[(item.fact_type, target, str(item.effective_date))].append(item)
    return tuple(
        Contradiction(
            fact_type=key[0], evidence_ids=tuple(sorted(item.evidence_id for item in items)),
            description="Sources give conflicting values for the same fact and effective date.",
        )
        for key, items in grouped.items()
        if len({canonical_json(item.normalized_value) for item in items}) > 1
        and len({item.citation.source_id for item in items}) > 1
    )


def independently_verify(
    evidence: tuple[EvidenceItem, ...], sources: tuple[Source, ...], output: Output,
) -> tuple[tuple[EvidenceItem, ...], tuple[Contradiction, ...]]:
    checks = {check.evidence_id: check for check in output.checks}
    verified: list[EvidenceItem] = []
    duplicate_checks = len(checks) != len(output.checks)
    for item in evidence:
        grounded, note = supports(item, sources)
        check = checks.get(item.evidence_id)
        accepted = grounded and not duplicate_checks and check is not None and check.supported
        verified.append(item.model_copy(update={
            "verified": accepted,
            "verifier_note": note if accepted else f"Rejected. {note}",
        }))
    result = tuple(verified)
    return result, contradictions_in(result)

from __future__ import annotations

import re
from datetime import date
from typing import Any, Protocol

from pydantic import JsonValue

from um_agentic.contracts import Citation, Contract, EvidenceItem, Source, SourceType
from um_agentic.errors import InvalidRequest
from um_agentic.serialization import content_hash


class Fact(Contract):
    fact_type: str
    normalized_value: JsonValue
    unit: str | None = None
    effective_date: date | None = None


class SourceStore(Protocol):
    def resolve(self, citation: Citation) -> JsonValue: ...


def element_at(resource: dict[str, JsonValue], path: str) -> JsonValue:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\[\d+\])?(?:\.[A-Za-z][A-Za-z0-9_]*(?:\[\d+\])?)*", path):
        raise InvalidRequest("Invalid source element path.")
    current: JsonValue = resource
    for name, index in re.findall(r"([A-Za-z][A-Za-z0-9_]*)(?:\[(\d+)\])?", path):
        if not isinstance(current, dict) or name not in current:
            raise InvalidRequest("Source element does not exist.")
        current = current[name]
        if index:
            if not isinstance(current, list) or int(index) >= len(current):
                raise InvalidRequest("Source element index does not exist.")
            current = current[int(index)]
    return current


class InMemorySources:
    def __init__(self, sources: tuple[Source, ...]) -> None:
        self.sources = {source.source_id: source for source in sources}

    def resolve(self, citation: Citation) -> JsonValue:
        source = self.sources.get(citation.source_id)
        if source is None or source.source_type != citation.source_type:
            raise InvalidRequest("Citation source does not exist or has a different type.")
        if source.source_type == SourceType.DOCUMENT:
            assert citation.page is not None
            assert citation.char_start is not None and citation.char_end is not None
            if citation.page > len(source.pages):
                raise InvalidRequest("Citation page does not exist.")
            page = source.pages[citation.page - 1]
            if citation.char_end > len(page):
                raise InvalidRequest("Citation span is outside the source page.")
            return page[citation.char_start:citation.char_end]
        assert source.resource is not None and citation.resource_path is not None
        return element_at(source.resource, citation.resource_path)


ROW = re.compile(
    r"^(Diagnosis|A1C|BMI|Metformin trial|Prior agent trial|Back pain duration|Neurologic deficit|Lifestyle program|Metformin contraindication):"
    r"\s*([^;\n]+?)(?:;\s*date:\s*(\d{4}-\d{2}-\d{2}))?\s*$",
    re.MULTILINE,
)


def document_facts(text: str) -> list[tuple[Fact, int, int]]:
    facts: list[tuple[Fact, int, int]] = []
    for match in ROW.finditer(text):
        label, raw, effective = match.groups()
        raw = raw.strip()
        effective_date = date.fromisoformat(effective) if effective else None
        value: JsonValue
        unit: str | None = None
        if label == "Diagnosis":
            if not re.fullmatch(r"[A-TV-Z][0-9][A-Z0-9](?:\.[A-Z0-9]{1,4})?", raw):
                continue
            fact_type, value = "diagnosis", raw
        elif label in {"A1C", "BMI"}:
            pattern = r"(\d+(?:\.\d+)?)\s*(%|kg/m2)"
            numeric = re.fullmatch(pattern, raw)
            if numeric is None:
                continue
            fact_type = "lab_a1c" if label == "A1C" else "bmi"
            value, unit = float(numeric[1]), numeric[2]
            if unit != ("%" if label == "A1C" else "kg/m2"):
                continue
        elif label in {"Metformin trial", "Prior agent trial"}:
            duration = re.fullmatch(r"(\d+)\s+days", raw)
            if duration is None:
                continue
            fact_type, unit = "therapy_duration", "days"
            value = {
                "drug_class": "BIGUANIDE" if label == "Metformin trial" else "PRIOR_AGENT",
                "days": int(duration[1]),
            }
        elif label == "Back pain duration":
            duration = re.fullmatch(r"(\d+)\s+days", raw)
            if duration is None:
                continue
            fact_type, value, unit = "back_pain_duration", int(duration[1]), "days"
        elif label == "Metformin contraindication":
            if raw.lower() not in {"yes", "no"}:
                continue
            fact_type, value = "contraindication", {"target": "metformin", "present": raw.lower() == "yes"}
        else:
            if raw.lower() not in {"yes", "no"}:
                continue
            fact_type = "neurologic_deficit" if label == "Neurologic deficit" else "lifestyle_program"
            value = raw.lower() == "yes"
        facts.append((
            Fact(fact_type=fact_type, normalized_value=value, unit=unit, effective_date=effective_date),
            match.start(), match.end(),
        ))
    return facts


def structured_facts(source: Source) -> list[tuple[Fact, str]]:
    resource = source.resource
    assert resource is not None
    if source.source_type == SourceType.CLAIMS:
        raw = resource.get("facts", [])
        if not isinstance(raw, list):
            raise InvalidRequest("Claims facts must be a list.")
        return [(Fact.model_validate(item), f"facts[{index}]") for index, item in enumerate(raw)]
    data: dict[str, Any] = resource
    resource_type = data.get("resourceType")
    if resource_type == "Claim":
        return [
            (
                Fact(fact_type="diagnosis", normalized_value=coding["code"]),
                f"diagnosis[{index}].diagnosisCodeableConcept.coding[0].code",
            )
            for index, diagnosis in enumerate(data.get("diagnosis", []))
            for coding in diagnosis.get("diagnosisCodeableConcept", {}).get("coding", [])[:1]
            if coding.get("system") == "http://hl7.org/fhir/sid/icd-10-cm"
        ]
    if resource_type == "Observation":
        codes = data.get("code", {}).get("coding", [])
        quantity = data.get("valueQuantity", {})
        if (
            any(c.get("system") == "http://loinc.org" and c.get("code") == "4548-4" for c in codes)
            and isinstance(quantity.get("value"), (int, float))
            and quantity.get("unit") == "%"
        ):
            return [(Fact(
                fact_type="lab_a1c", normalized_value=quantity["value"], unit="%",
                effective_date=date.fromisoformat(data["effectiveDateTime"][:10])
                if data.get("effectiveDateTime") else None,
            ), "valueQuantity")]
    if resource_type == "Condition":
        codes = data.get("code", {}).get("coding", [])
        if codes and codes[0].get("system") == "http://hl7.org/fhir/sid/icd-10-cm":
            return [(
                Fact(fact_type="diagnosis", normalized_value=codes[0]["code"]),
                "code.coding[0].code",
            )]
    return []


def extract_sources(sources: tuple[Source, ...]) -> tuple[EvidenceItem, ...]:
    evidence: list[EvidenceItem] = []
    for source in sources:
        located: list[tuple[Fact, Citation]] = []
        if source.source_type == SourceType.DOCUMENT:
            for page_number, text in enumerate(source.pages, start=1):
                located.extend(
                    (fact, Citation(
                        source_type=source.source_type, source_id=source.source_id,
                        page=page_number, char_start=start, char_end=end,
                    ))
                    for fact, start, end in document_facts(text)
                )
        else:
            located.extend(
                (fact, Citation(
                    source_type=source.source_type, source_id=source.source_id, resource_path=path,
                ))
                for fact, path in structured_facts(source)
            )
        for fact, citation in located:
            identity = {**fact.model_dump(mode="json"), "citation": citation.model_dump(mode="json")}
            evidence.append(EvidenceItem(
                evidence_id=f"evidence_{content_hash(identity)[:24]}",
                **fact.model_dump(), citation=citation, extracted_by="clinical_evidence",
            ))
    return tuple(evidence)


def supports(evidence: EvidenceItem, sources: tuple[Source, ...]) -> tuple[bool, str]:
    try:
        cited = InMemorySources(sources).resolve(evidence.citation)
    except InvalidRequest as exc:
        return False, str(exc)
    expected = Fact(
        fact_type=evidence.fact_type, normalized_value=evidence.normalized_value,
        unit=evidence.unit, effective_date=evidence.effective_date,
    )
    if evidence.citation.source_type == SourceType.DOCUMENT:
        if not isinstance(cited, str):
            return False, "Document citation is not text."
        matches = [fact for fact, _, _ in document_facts(cited)]
    else:
        source = next(s for s in sources if s.source_id == evidence.citation.source_id)
        matches = [
            fact for fact, path in structured_facts(source)
            if path == evidence.citation.resource_path
        ]
    if expected in matches:
        return True, "Source location and normalized claim independently verified."
    return False, "The cited source does not support the normalized claim."


def minimum_sources(sources: tuple[Source, ...]) -> tuple[Source, ...]:
    minimized: list[Source] = []
    for source in sources:
        if source.source_type != SourceType.FHIR:
            minimized.append(source)
            continue
        assert source.resource is not None
        allowed = {
            "resourceType", "id", "diagnosis", "code", "valueQuantity",
            "effectiveDateTime", "effectivePeriod", "medicationCodeableConcept",
        }
        minimized.append(source.model_copy(update={
            "resource": {key: value for key, value in source.resource.items() if key in allowed},
        }))
    return tuple(minimized)

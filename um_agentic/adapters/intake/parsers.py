from datetime import date
from typing import Any, Literal

from fhir.resources.R4B.bundle import Bundle
from pydantic import AwareDatetime, Field, JsonValue, ValidationError

from um_agentic.contracts import (
    Channel,
    Contract,
    LineOfBusiness,
    Provider,
    Service,
    Source,
    SourceType,
    Urgency,
)
from um_agentic.errors import InvalidRequest


class CanonicalRequest(Contract):
    member_token: str = Field(pattern=r"^(member_|tok_)[A-Za-z0-9_]{3,100}$")
    date_of_service: date
    urgency: Urgency = Urgency.STANDARD
    requesting_provider: Provider
    service: Service
    diagnoses: tuple[str, ...] = ()


class IntakeEnvelope(Contract):
    channel: Channel
    line_of_business: LineOfBusiness
    received_at: AwareDatetime
    jurisdiction: str = "DEFAULT"
    payload: dict[str, JsonValue]
    sources: tuple[Source, ...] = ()


class NormalizedIntake(Contract):
    channel: Channel
    request: CanonicalRequest
    sources: tuple[Source, ...]
    original_payload: dict[str, JsonValue]
    parser_version: Literal["2026.1"] = "2026.1"

    def round_trip(self) -> dict[str, JsonValue]:
        return self.original_payload


class Parsed278(Contract):
    transaction: Literal["278"]
    member_token: str
    service_date: date
    urgency_code: Literal["R", "I"]
    provider: Provider
    service: Service
    diagnoses: tuple[str, ...] = ()


class ParsedEPA(Contract):
    standard: Literal["SCRIPT_2017071"]
    patient_token: str
    service_date: date
    expedited: bool
    prescriber: Provider
    medication: Service
    diagnoses: tuple[str, ...] = ()


class FaxFields(Contract):
    fields: CanonicalRequest


def _fhir(payload: dict[str, Any]) -> tuple[CanonicalRequest, tuple[Source, ...]]:
    bundle = Bundle.model_validate(payload)
    if bundle.type not in {"collection", "message"}:
        raise InvalidRequest("The reference PAS adapter expects a collection or message Bundle.")
    resources = [entry.resource for entry in bundle.entry or () if entry.resource is not None]
    claims = [resource for resource in resources if resource.get_resource_type() == "Claim"]
    if len(claims) != 1:
        raise InvalidRequest("Exactly one Claim is required per authorization request.")
    claim = claims[0].model_dump(mode="json", exclude_none=True)
    if claim["use"] != "preauthorization" or len(claim.get("item", [])) != 1:
        raise InvalidRequest("The reference adapter accepts one preauthorization service per case.")
    indexed = {
        f"{resource.get_resource_type()}/{resource.id}":
        resource.model_dump(mode="json", exclude_none=True)
        for resource in resources if resource.id
    }
    patient = indexed.get(claim["patient"].get("reference", ""))
    provider = indexed.get(claim["provider"].get("reference", ""))
    if patient is None or provider is None:
        raise InvalidRequest("Patient and provider references must resolve inside the Bundle.")
    member_ids = [
        item["value"] for item in patient.get("identifier", [])
        if item.get("system") == "urn:um:member-token" and item.get("value")
    ]
    npis = [
        item["value"] for item in provider.get("identifier", [])
        if item.get("system") == "http://hl7.org/fhir/sid/us-npi" and item.get("value")
    ]
    if len(member_ids) != 1 or len(npis) != 1:
        raise InvalidRequest("One member token and one requesting provider NPI are required.")
    item = claim["item"][0]
    codings = item["productOrService"].get("coding", [])
    if len(codings) != 1 or not item.get("servicedDate"):
        raise InvalidRequest("One service coding and a service date are required.")
    diagnoses: list[str] = []
    for diagnosis in claim.get("diagnosis", []):
        codes = diagnosis.get("diagnosisCodeableConcept", {}).get("coding", [])
        if len(codes) != 1 or codes[0].get("system") != "http://hl7.org/fhir/sid/icd-10-cm":
            raise InvalidRequest("Each diagnosis requires one ICD10 CM coding.")
        diagnoses.append(codes[0]["code"])
    priorities = claim.get("priority", {}).get("coding", [])
    priority = priorities[0].get("code") if priorities else None
    if priority not in {"normal", "urgent", "stat"}:
        raise InvalidRequest("An explicit normal, urgent, or stat priority is required.")
    days_supply = next(
        (e.get("valuePositiveInt") for e in item.get("extension", [])
         if e.get("url") == "urn:um:days-supply"),
        None,
    )
    request = CanonicalRequest(
        member_token=member_ids[0],
        date_of_service=item["servicedDate"],
        urgency=Urgency.EXPEDITED if priority in {"urgent", "stat"} else Urgency.STANDARD,
        requesting_provider=Provider(npi=npis[0], specialty="Primary care"),
        service=Service(
            code_system=codings[0]["system"], code=codings[0]["code"],
            quantity=item.get("quantity", {}).get("value", 1),
            days_supply=days_supply,
            place_of_service=item.get("locationCodeableConcept", {}).get("text"),
        ),
        diagnoses=tuple(diagnoses),
    )
    sources = tuple(
        Source(source_id=key, source_type=SourceType.FHIR, resource=value)
        for key, value in indexed.items()
        if value["resourceType"] in {"Claim", "Observation", "MedicationStatement", "Condition"}
    )
    return request, sources


def normalize(envelope: IntakeEnvelope) -> NormalizedIntake:
    extra_sources: tuple[Source, ...] = ()
    try:
        if envelope.channel == Channel.FHIR:
            request, extra_sources = _fhir(envelope.payload)
        elif envelope.channel == Channel.X12:
            x12 = Parsed278.model_validate(envelope.payload)
            request = CanonicalRequest(
                member_token=x12.member_token, date_of_service=x12.service_date,
                urgency=Urgency.EXPEDITED if x12.urgency_code == "I" else Urgency.STANDARD,
                requesting_provider=x12.provider, service=x12.service, diagnoses=x12.diagnoses,
            )
        elif envelope.channel == Channel.NCPDP:
            epa = ParsedEPA.model_validate(envelope.payload)
            request = CanonicalRequest(
                member_token=epa.patient_token, date_of_service=epa.service_date,
                urgency=Urgency.EXPEDITED if epa.expedited else Urgency.STANDARD,
                requesting_provider=epa.prescriber, service=epa.medication, diagnoses=epa.diagnoses,
            )
        else:
            request = FaxFields.model_validate(envelope.payload).fields
    except (ValidationError, KeyError, TypeError) as exc:
        raise InvalidRequest("The channel payload is incomplete or invalid.") from exc
    sources = (*envelope.sources, *extra_sources)
    if len({s.source_id for s in sources}) != len(sources):
        raise InvalidRequest("Source identifiers must be unique within a case.")
    return NormalizedIntake(
        channel=envelope.channel, request=request,
        sources=sources, original_payload=envelope.payload,
    )

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_serializer,
    field_validator,
    model_validator,
)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class Channel(StrEnum):
    FHIR = "FHIR"
    X12 = "X12"
    NCPDP = "NCPDP"
    FAX = "FAX"


class LineOfBusiness(StrEnum):
    PHARMACY = "PHARMACY"
    MEDICAL = "MEDICAL"


class Urgency(StrEnum):
    EXPEDITED = "EXPEDITED"
    STANDARD = "STANDARD"


class Status(StrEnum):
    RECEIVED = "RECEIVED"
    NORMALIZED = "NORMALIZED"
    ADMIN_CHECK = "ADMIN_CHECK"
    NO_PA_REQUIRED = "NO_PA_REQUIRED"
    ADMIN_PEND = "ADMIN_PEND"
    DOCS_CHECK = "DOCS_CHECK"
    PEND_MISSING_INFO = "PEND_MISSING_INFO"
    CLINICAL_ANALYSIS = "CLINICAL_ANALYSIS"
    VERIFICATION = "VERIFICATION"
    RECOMMENDED = "RECOMMENDED"
    AUTO_APPROVED = "AUTO_APPROVED"
    IN_CLINICAL_REVIEW = "IN_CLINICAL_REVIEW"
    IN_PHYSICIAN_REVIEW = "IN_PHYSICIAN_REVIEW"
    PEER_TO_PEER_SCHEDULED = "PEER_TO_PEER_SCHEDULED"
    DETERMINED = "DETERMINED"
    NOTIFICATION_PENDING_APPROVAL = "NOTIFICATION_PENDING_APPROVAL"
    NOTIFIED = "NOTIFIED"
    CLOSED = "CLOSED"
    WITHDRAWN = "WITHDRAWN"


class Role(StrEnum):
    INTAKE = "intake"
    NURSE = "nurse_reviewer"
    PHYSICIAN = "physician_reviewer"
    PHARMACIST = "pharmacist_reviewer"
    SUPERVISOR = "supervisor"
    AUDITOR = "auditor"
    ADMIN = "admin"


class ActorKind(StrEnum):
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"
    AGENT = "AGENT"


class Actor(Contract):
    actor_id: str = Field(min_length=1, max_length=200)
    kind: ActorKind
    roles: frozenset[Role] = frozenset()

    @field_serializer("roles", when_used="json")
    def sorted_roles(self, roles: frozenset[Role]) -> list[str]:
        return sorted(role.value for role in roles)


SYSTEM = Actor(actor_id="workflow", kind=ActorKind.SYSTEM)


class Provider(Contract):
    npi: str = Field(pattern=r"^\d{10}$")
    specialty: str = Field(min_length=1, max_length=100)


class Service(Contract):
    code_system: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=100)
    quantity: float = Field(default=1, gt=0, allow_inf_nan=False)
    days_supply: int | None = Field(default=None, gt=0)
    place_of_service: str | None = None


class DocumentRef(Contract):
    document_id: str = Field(min_length=1, max_length=200)
    media_type: Literal["text/plain", "application/pdf", "application/fhir+json"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    page_count: int = Field(default=1, ge=1)
    storage_key: str | None = None


class Clock(Contract):
    due_at: AwareDatetime
    percent_elapsed: float = Field(default=0, ge=0, allow_inf_nan=False)
    paused: bool = False
    sla_hours: float = Field(gt=0, allow_inf_nan=False)
    pause_started_at: AwareDatetime | None = None
    total_paused_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    warning_emitted: bool = False

    @model_validator(mode="after")
    def coherent_pause(self) -> Clock:
        if self.paused != (self.pause_started_at is not None):
            raise ValueError("A paused clock requires its pause start time.")
        return self


class UMCase(Contract):
    case_id: UUID = Field(default_factory=uuid4)
    channel: Channel
    line_of_business: LineOfBusiness
    urgency: Urgency
    received_at: AwareDatetime
    member_token: str = Field(pattern=r"^(member_|tok_)[A-Za-z0-9_]{3,100}$")
    jurisdiction: str = Field(default="DEFAULT", min_length=1, max_length=30)
    date_of_service: date
    requesting_provider: Provider
    service: Service
    diagnoses: tuple[str, ...] = ()
    attachments: tuple[DocumentRef, ...] = ()
    status: Status = Status.RECEIVED
    clock: Clock

    @field_validator("diagnoses")
    @classmethod
    def valid_diagnoses(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not re.fullmatch(r"[A-TV-Z][0-9][A-Z0-9](?:\.[A-Z0-9]{1,4})?", v) for v in values):
            raise ValueError("Diagnoses must be ICD10 codes.")
        return values


class SourceType(StrEnum):
    DOCUMENT = "DOCUMENT"
    FHIR = "FHIR"
    CLAIMS = "CLAIMS"


class Citation(Contract):
    source_type: SourceType
    source_id: str = Field(min_length=1, max_length=200)
    page: int | None = Field(default=None, ge=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=1)
    resource_path: str | None = None

    @model_validator(mode="after")
    def complete_location(self) -> Citation:
        if self.source_type == SourceType.DOCUMENT:
            if self.page is None or self.char_start is None or self.char_end is None:
                raise ValueError("Document citations require a page and character span.")
            if self.char_end <= self.char_start or self.resource_path is not None:
                raise ValueError("Document citation span is invalid.")
        elif not self.resource_path or any(
            value is not None for value in (self.page, self.char_start, self.char_end)
        ):
            raise ValueError("Structured citations require only an element path.")
        return self


class EvidenceItem(Contract):
    evidence_id: str = Field(min_length=1, max_length=200)
    fact_type: str = Field(pattern=r"^[a-z][a-z0-9_]{0,80}$")
    normalized_value: JsonValue
    unit: str | None = None
    effective_date: date | None = None
    citation: Citation
    extracted_by: str
    verified: bool = False
    verifier_note: str | None = None
    extraction_score: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)


class EvidenceSelector(Contract):
    fact_type: str
    code_prefix: str | None = None
    target: str | None = None


class ValueRule(Contract):
    fact_type: str
    op: Literal["gt", "gte", "lt", "lte", "eq"]
    value: float | bool | str
    within_days: int | None = Field(default=None, ge=0)


class TherapyRule(Contract):
    drug_class: str
    min_days: int = Field(ge=1)


class Rule(Contract):
    any_evidence: EvidenceSelector | None = None
    evidence_value: ValueRule | None = None
    therapy_duration: TherapyRule | None = None
    any_of: tuple[Rule, ...] | None = None
    all_of: tuple[Rule, ...] | None = None

    @model_validator(mode="after")
    def exactly_one_operator(self) -> Rule:
        if sum(v is not None for v in (
            self.any_evidence, self.evidence_value, self.therapy_duration,
            self.any_of, self.all_of,
        )) != 1:
            raise ValueError("A rule requires exactly one operator.")
        if self.any_of == () or self.all_of == ():
            raise ValueError("Compound rules cannot be empty.")
        return self


class Criterion(Contract):
    id: str
    description: str
    rule: Rule | None = None
    requires_clinical_judgment: bool = False
    question: str | None = None

    @model_validator(mode="after")
    def has_rule(self) -> Criterion:
        if self.rule is None and not self.requires_clinical_judgment:
            raise ValueError("A deterministic criterion requires a rule.")
        return self


class AppliesTo(Contract):
    line: LineOfBusiness
    codes: CodeSelection


class CodeSelection(Contract):
    system: str
    values: tuple[str, ...] = Field(min_length=1)


class CriteriaLogic(Contract):
    all_of: tuple[str, ...] | None = None
    any_of: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def one_logic(self) -> CriteriaLogic:
        if (self.all_of is None) == (self.any_of is None):
            raise ValueError("Criteria logic requires exactly one nonempty operator.")
        if not (self.all_of or self.any_of):
            raise ValueError("Criteria logic cannot be empty.")
        return self


class CriteriaSet(Contract):
    criteria_set_id: str
    version: str = Field(pattern=r"^\d{4}\.\d+$")
    applies_to: AppliesTo
    auto_approve_eligible: bool = False
    criteria: tuple[Criterion, ...] = Field(min_length=1)
    logic: CriteriaLogic

    @model_validator(mode="after")
    def referenced_criteria_exist(self) -> CriteriaSet:
        ids = [c.id for c in self.criteria]
        referenced = self.logic.all_of or self.logic.any_of or ()
        if len(set(ids)) != len(ids) or set(referenced) != set(ids):
            raise ValueError("Logic must reference every criterion exactly once.")
        if len(set(referenced)) != len(referenced):
            raise ValueError("Repeated criterion reference.")
        return self


class CriterionStatus(StrEnum):
    MET = "MET"
    NOT_MET = "NOT_MET"
    UNKNOWN = "UNKNOWN"


class CriterionResult(Contract):
    criterion_id: str
    status: CriterionStatus
    evidence_ids: tuple[str, ...] = ()
    rationale: str


class CriteriaEvaluation(Contract):
    criteria_set_id: str
    version: str
    results: tuple[CriterionResult, ...]
    overall: Literal["ALL_MET", "GAPS", "INDETERMINATE"]


class AgentOutcome(StrEnum):
    APPROVE_RECOMMENDED = "APPROVE_RECOMMENDED"
    PEND_CLINICAL_REVIEW = "PEND_CLINICAL_REVIEW"
    PEND_MISSING_INFO = "PEND_MISSING_INFO"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ConfidenceComponents(Contract):
    all_required_met: bool
    verified_evidence_count: int = Field(ge=0)
    contradiction_count: int = Field(ge=0)
    unstructured_source_count: int = Field(ge=0)
    min_extraction_score: float = Field(ge=0, le=1)
    clinical_judgment_required: bool = False
    agent_failed: bool = False
    explanation: str


class Recommendation(Contract):
    outcome: AgentOutcome
    confidence: Confidence
    components: ConfidenceComponents
    reasons: tuple[str, ...] = Field(min_length=1)
    open_questions: tuple[str, ...] = ()
    routing_queue: Literal["nurse", "physician", "administrative"]


class DeterminationOutcome(StrEnum):
    APPROVED = "APPROVED"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    DENIED = "DENIED"
    WITHDRAWN = "WITHDRAWN"


class Determination(Contract):
    outcome: DeterminationOutcome
    approved_quantity: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    approved_duration_days: int | None = Field(default=None, gt=0)
    reviewer_id: str
    reviewer_role: Role
    specific_reason_code: str | None = None
    plain_language_reason: str | None = None
    criteria_cited: tuple[str, ...] = ()
    override_of_recommendation: bool = False
    override_rationale: str | None = None

    @model_validator(mode="after")
    def complete_decision(self) -> Determination:
        if self.outcome != DeterminationOutcome.APPROVED:
            if not (self.specific_reason_code or "").strip():
                raise ValueError("A specific reason code is required.")
            if not (self.plain_language_reason or "").strip():
                raise ValueError("A plain language reason is required.")
        if self.outcome in (DeterminationOutcome.APPROVED, DeterminationOutcome.PARTIALLY_APPROVED):
            if self.approved_quantity is None or self.approved_duration_days is None:
                raise ValueError("Approval requires quantity and duration.")
        elif self.approved_quantity is not None or self.approved_duration_days is not None:
            raise ValueError("An adverse determination cannot carry approved units.")
        if self.override_of_recommendation and not (self.override_rationale or "").strip():
            raise ValueError("An override requires a rationale.")
        return self


class Source(Contract):
    source_id: str
    source_type: SourceType
    pages: tuple[str, ...] = ()
    resource: dict[str, JsonValue] | None = None
    document_ref: DocumentRef | None = None

    @model_validator(mode="after")
    def source_content(self) -> Source:
        if self.source_type == SourceType.DOCUMENT:
            if not self.pages or self.resource is not None:
                raise ValueError("Documents require pages, not structured resources.")
        elif self.resource is None or self.pages:
            raise ValueError("Structured sources require a resource.")
        if self.document_ref is not None and (
            self.source_type != SourceType.DOCUMENT
            or self.document_ref.document_id != self.source_id
            or self.document_ref.page_count != len(self.pages)
        ):
            raise ValueError("Document metadata must match its source.")
        return self


class CoverageResult(Contract):
    eligible: bool
    benefit_covered: bool
    pa_required: bool
    reason: str
    checked_date: date


class Contradiction(Contract):
    fact_type: str
    evidence_ids: tuple[str, ...] = Field(min_length=2)
    description: str


class VerificationCheck(Contract):
    evidence_id: str
    supported: bool
    note: str


class ExtractionOutput(Contract):
    evidence: tuple[EvidenceItem, ...]


class VerificationOutput(Contract):
    checks: tuple[VerificationCheck, ...]
    contradictions: tuple[Contradiction, ...] = ()


class DTRQuestion(Contract):
    criterion_id: str
    text: str = Field(min_length=1)
    answer_type: Literal["text", "date", "number", "boolean"] = "text"


class DocumentationOutput(Contract):
    questions: tuple[DTRQuestion, ...]


class CriteriaMappingOutput(Contract):
    results: tuple[CriterionResult, ...]


class SummaryOutput(Contract):
    summary: str
    evidence_ids: tuple[str, ...] = ()


class LetterDraftOutput(Contract):
    member_text: str
    provider_text: str
    language: str = "en"


class InsightsOutput(Contract):
    findings: tuple[str, ...]
    advisory_only: Literal[True] = True


class InformationRequest(Contract):
    questions: tuple[DTRQuestion, ...] = Field(min_length=1)
    requested_by: str
    requested_at: AwareDatetime
    delivery_status: Literal["LOCAL_RECORDED", "SENT"] = "LOCAL_RECORDED"
    received_at: AwareDatetime | None = None


class Letter(Contract):
    letter_id: UUID = Field(default_factory=uuid4)
    recipient: Literal["member", "provider"]
    text: str
    language: str = "en"
    status: Literal["DRAFT", "RELEASED"] = "DRAFT"
    approved_by: str | None = None
    released_at: AwareDatetime | None = None
    sha256: str | None = None


class CaseAggregate(Contract):
    case: UMCase
    original_request: dict[str, JsonValue]
    coverage: CoverageResult | None = None
    sources: tuple[Source, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    contradictions: tuple[Contradiction, ...] = ()
    injection_sources: tuple[str, ...] = ()
    criteria_snapshot: CriteriaSet | None = None
    evaluation: CriteriaEvaluation | None = None
    recommendation: Recommendation | None = None
    documentation: DocumentationOutput | None = None
    reviewer_summary: SummaryOutput | None = None
    determination: Determination | None = None
    information_requests: tuple[InformationRequest, ...] = ()
    letters: tuple[Letter, ...] = ()
    agent_run_ids: tuple[UUID, ...] = ()
    failures: tuple[str, ...] = ()
    automatic_approval: bool = False
    peer_to_peer_at: AwareDatetime | None = None
    closed_at: AwareDatetime | None = None


class AgentRun(Contract):
    run_id: UUID = Field(default_factory=uuid4)
    case_id: UUID | None
    agent: str
    model_id: str
    prompt_version: str
    prompt_hash: str
    prompt_text: str = ""
    criteria_version: str | None
    inputs_hash: str
    inputs: dict[str, JsonValue]
    outputs: dict[str, JsonValue] | None
    outputs_hash: str | None
    raw_outputs: tuple[str, ...] = ()
    responses: tuple[dict[str, JsonValue], ...] = ()
    latency_ms: float = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    attempts: int = Field(ge=1, le=2)
    error: str | None = None


class Event(Contract):
    event_id: UUID
    case_id: UUID
    sequence: int
    event_type: str
    actor: Actor
    occurred_at: AwareDatetime
    reason: str
    payload: dict[str, JsonValue]
    payload_hash: str
    previous_hash: str
    event_hash: str


AGENT_OUTPUT_MODELS: tuple[type[Contract], ...] = (
    ExtractionOutput, VerificationOutput, DocumentationOutput, CriteriaMappingOutput,
    Recommendation, SummaryOutput, LetterDraftOutput, InsightsOutput,
)
SCHEMA_MODELS: tuple[type[Contract], ...] = (
    UMCase, EvidenceItem, Criterion, CriteriaSet, CriteriaEvaluation, Recommendation,
    Determination, CaseAggregate, AgentRun, Event, *AGENT_OUTPUT_MODELS,
)

NonemptyText = Annotated[str, Field(min_length=1)]

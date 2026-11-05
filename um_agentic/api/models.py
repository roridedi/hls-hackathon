from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from um_agentic.contracts import (
    Citation,
    Confidence,
    Contract,
    DeterminationOutcome,
    DTRQuestion,
    LineOfBusiness,
    Source,
    Status,
    Urgency,
)


class QueueItem(Contract):
    case_id: UUID
    status: Status
    line_of_business: LineOfBusiness
    service_code: str
    urgency: Urgency
    due_at: AwareDatetime
    percent_elapsed: float
    confidence: Confidence | None


class DeterminationRequest(Contract):
    outcome: DeterminationOutcome
    approved_quantity: float | None = Field(default=None, gt=0)
    approved_duration_days: int | None = Field(default=None, gt=0)
    specific_reason_code: str | None = None
    plain_language_reason: str | None = None
    criteria_cited: tuple[str, ...] = ()
    override_rationale: str | None = None


class ActionReason(Contract):
    reason: str = Field(min_length=3, max_length=4000)


class InformationRequestBody(Contract):
    questions: tuple[DTRQuestion, ...] = Field(min_length=1)


class InformationReceived(Contract):
    sources: tuple[Source, ...] = Field(min_length=1)


class PeerToPeerRequest(Contract):
    scheduled_at: AwareDatetime
    reason: str = Field(min_length=3)


class LetterRelease(Contract):
    confirm: Literal[True]
    member_text: str | None = Field(default=None, min_length=1)
    provider_text: str | None = Field(default=None, min_length=1)


class CopilotQuestion(Contract):
    question: str = Field(min_length=1, max_length=2000)


class CitationView(Contract):
    citation: Citation
    text: str
    highlight_start: int
    highlight_end: int


class ReviewStarted(Contract):
    started_at: datetime

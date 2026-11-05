from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request

from um_agentic.adapters.sources import InMemorySources
from um_agentic.api.auth import Authenticated
from um_agentic.api.models import (
    ActionReason,
    CitationView,
    CopilotQuestion,
    DeterminationRequest,
    InformationReceived,
    InformationRequestBody,
    LetterRelease,
    PeerToPeerRequest,
    QueueItem,
)
from um_agentic.contracts import CaseAggregate, SourceType, Status, SummaryOutput
from um_agentic.errors import NotFound
from um_agentic.security.roles import READ_ROLES, require_roles
from um_agentic.serialization import canonical_json

router = APIRouter()


@router.get("/queues", response_model=dict[str, list[QueueItem]])
def queues(request: Request, actor: Authenticated) -> dict[str, list[QueueItem]]:
    return request.app.state.review.queues(actor)


@router.get("/cases/{case_id}/questionnaire")
def questionnaire(case_id: UUID, request: Request, actor: Authenticated) -> dict:
    require_roles(actor, READ_ROLES)
    current = request.app.state.store.get(case_id)
    questions = current.documentation.questions if current.documentation else ()
    return {
        "resourceType": "Questionnaire", "id": f"dtr-{case_id}", "status": "draft",
        "version": current.criteria_snapshot.version if current.criteria_snapshot else "2026.1",
        "item": [
            {"linkId": q.criterion_id, "text": q.text, "type": {
                "text": "string", "date": "date", "number": "decimal", "boolean": "boolean",
            }[q.answer_type], "required": True}
            for q in questions
        ],
    }


@router.get("/cases/{case_id}/evidence/{evidence_id}/source", response_model=CitationView)
def source(
    case_id: UUID, evidence_id: str, request: Request, actor: Authenticated,
) -> CitationView:
    require_roles(actor, READ_ROLES)
    current = request.app.state.store.get(case_id)
    item = next((e for e in current.evidence if e.evidence_id == evidence_id), None)
    if item is None:
        raise NotFound("Evidence item not found.")
    resolved = InMemorySources(current.sources).resolve(item.citation)
    if item.citation.source_type == SourceType.DOCUMENT:
        document = next(s for s in current.sources if s.source_id == item.citation.source_id)
        assert item.citation.page is not None
        text = document.pages[item.citation.page - 1]
        start, end = item.citation.char_start, item.citation.char_end
        assert start is not None and end is not None
    else:
        text, start = canonical_json(resolved), 0
        end = len(text)
    return CitationView(citation=item.citation, text=text, highlight_start=start, highlight_end=end)


@router.post("/cases/{case_id}/actions/start-review", status_code=204)
def start_review(case_id: UUID, request: Request, actor: Authenticated) -> None:
    request.app.state.review._clinical(case_id, actor)
    request.app.state.workflow.record(
        case_id, actor, "review_started", "Human reviewer opened the work item.",
        {"started_at": datetime.now(UTC).isoformat()},
    )


@router.post("/cases/{case_id}/actions/accept", response_model=CaseAggregate)
def accept(case_id: UUID, request: Request, actor: Authenticated) -> CaseAggregate:
    return request.app.state.review.accept(case_id, actor)


@router.post("/cases/{case_id}/actions/determine", response_model=CaseAggregate)
def determine(
    case_id: UUID, body: DeterminationRequest, request: Request, actor: Authenticated,
) -> CaseAggregate:
    return request.app.state.review.determine(case_id, actor, body)


@router.post("/cases/{case_id}/actions/request-info", response_model=CaseAggregate)
def request_info(
    case_id: UUID, body: InformationRequestBody, request: Request, actor: Authenticated,
) -> CaseAggregate:
    return request.app.state.review.request_info(case_id, actor, body.questions)


@router.post("/cases/{case_id}/actions/information", response_model=CaseAggregate)
def information(
    case_id: UUID, body: InformationReceived, request: Request, actor: Authenticated,
) -> CaseAggregate:
    return request.app.state.review.receive_info(case_id, actor, body.sources)


@router.post("/cases/{case_id}/actions/escalate", response_model=CaseAggregate)
def escalate(
    case_id: UUID, body: ActionReason, request: Request, actor: Authenticated,
) -> CaseAggregate:
    return request.app.state.review.escalate(case_id, actor, body.reason)


@router.post("/cases/{case_id}/actions/peer-to-peer", response_model=CaseAggregate)
def peer_to_peer(
    case_id: UUID, body: PeerToPeerRequest, request: Request, actor: Authenticated,
) -> CaseAggregate:
    return request.app.state.review.schedule_peer(case_id, actor, body.scheduled_at, body.reason)


@router.post("/cases/{case_id}/actions/complete-peer", response_model=CaseAggregate)
def complete_peer(
    case_id: UUID, body: ActionReason, request: Request, actor: Authenticated,
) -> CaseAggregate:
    request.app.state.review._clinical(case_id, actor)
    return request.app.state.workflow.transition(
        case_id, Status.IN_PHYSICIAN_REVIEW, actor, body.reason,
    )


@router.post("/cases/{case_id}/actions/withdraw", response_model=CaseAggregate)
def withdraw(
    case_id: UUID, body: ActionReason, request: Request, actor: Authenticated,
) -> CaseAggregate:
    return request.app.state.review.withdraw(case_id, actor, body.reason)


@router.post("/cases/{case_id}/actions/draft-letters", response_model=CaseAggregate)
def draft_letters(case_id: UUID, request: Request, actor: Authenticated) -> CaseAggregate:
    return request.app.state.review.manual_draft(case_id, actor)


@router.post("/cases/{case_id}/actions/release", response_model=CaseAggregate)
def release(
    case_id: UUID, body: LetterRelease, request: Request, actor: Authenticated,
) -> CaseAggregate:
    return request.app.state.review.release(case_id, actor, body)


@router.post("/cases/{case_id}/copilot", response_model=SummaryOutput)
async def copilot(
    case_id: UUID, body: CopilotQuestion, request: Request, actor: Authenticated,
) -> SummaryOutput:
    return await request.app.state.review.copilot(case_id, actor, body.question)

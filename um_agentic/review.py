import asyncio
import hashlib
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError

from um_agentic.agents.letter.runner import SPEC as LETTER
from um_agentic.agents.reviewer_copilot.runner import SPEC as COPILOT
from um_agentic.api.models import DeterminationRequest, LetterRelease, QueueItem
from um_agentic.contracts import (
    SYSTEM,
    Actor,
    CaseAggregate,
    Determination,
    DeterminationOutcome,
    DTRQuestion,
    InformationRequest,
    Letter,
    Role,
    Source,
    Status,
    SummaryOutput,
)
from um_agentic.errors import AgentFailure, Conflict, InvalidRequest
from um_agentic.letters.templates import draft_text, validate_release_text
from um_agentic.security.roles import CLINICAL_ROLES, INTAKE_ROLES, READ_ROLES, require_roles
from um_agentic.serialization import canonical_json
from um_agentic.service import UMService
from um_agentic.workflow.clocks import refresh_clock
from um_agentic.workflow.transitions import TransitionContext, clinical_human, physician_human

QUEUE_STATES = {
    "administrative": {Status.ADMIN_PEND, Status.PEND_MISSING_INFO},
    "nurse": {Status.IN_CLINICAL_REVIEW},
    "physician": {Status.IN_PHYSICIAN_REVIEW},
    "peer_to_peer": {Status.PEER_TO_PEER_SCHEDULED},
    "letters": {Status.NOTIFICATION_PENDING_APPROVAL},
}
RELEASE_ROLES = CLINICAL_ROLES | {Role.SUPERVISOR}


class ReviewService:
    def __init__(self, service: UMService) -> None:
        self.service = service
        self.store = service.store
        self.workflow = service.workflow
        self.config = service.config

    def queues(self, actor: Actor) -> dict[str, list[QueueItem]]:
        require_roles(actor, READ_ROLES)
        queues: dict[str, list[QueueItem]] = {name: [] for name in QUEUE_STATES}
        now = datetime.now(UTC)
        for aggregate in self.store.all_cases():
            case = aggregate.case
            for name, states in QUEUE_STATES.items():
                if case.status in states:
                    queues[name].append(QueueItem(
                        case_id=case.case_id, status=case.status,
                        line_of_business=case.line_of_business, service_code=case.service.code,
                        urgency=case.urgency, due_at=case.clock.due_at,
                        percent_elapsed=refresh_clock(case, now).percent_elapsed,
                        confidence=aggregate.recommendation.confidence if aggregate.recommendation else None,
                    ))
        for queue in queues.values():
            queue.sort(key=lambda item: (item.due_at, str(item.case_id)))
        return queues

    def _clinical(self, case_id: UUID, actor: Actor) -> CaseAggregate:
        require_roles(actor, CLINICAL_ROLES)
        current = self.store.get(case_id)
        clinical_human(TransitionContext(current, actor, self.config))
        return current

    def determine(
        self, case_id: UUID, actor: Actor, request: DeterminationRequest,
    ) -> CaseAggregate:
        current = self._clinical(case_id, actor)
        adverse = request.outcome != DeterminationOutcome.APPROVED
        if adverse:
            physician_human(TransitionContext(current, actor, self.config))
        if current.case.status not in {Status.IN_CLINICAL_REVIEW, Status.IN_PHYSICIAN_REVIEW}:
            raise Conflict("A determination requires an active clinical review.")
        criteria_ids = {c.id for c in current.criteria_snapshot.criteria} if current.criteria_snapshot else set()
        cited = request.criteria_cited or tuple(sorted(criteria_ids))
        if not set(cited) <= criteria_ids:
            raise InvalidRequest("The determination cites an unknown criterion.")
        recommended_approval = bool(
            current.recommendation and current.recommendation.outcome.value == "APPROVE_RECOMMENDED"
        )
        override = recommended_approval != (request.outcome == DeterminationOutcome.APPROVED)
        if adverse:
            licensed = actor.roles & self.config.adverse_reviewer_roles
            reviewer_role = Role.PHYSICIAN if Role.PHYSICIAN in licensed else Role.PHARMACIST
        else:
            reviewer_role = next(role for role in (
                Role.PHYSICIAN, Role.PHARMACIST, Role.NURSE,
            ) if role in actor.roles)
        try:
            determination = Determination(
                **request.model_dump(exclude={"override_rationale", "criteria_cited"}),
                reviewer_id=actor.actor_id, reviewer_role=reviewer_role, criteria_cited=cited,
                override_of_recommendation=override,
                override_rationale=request.override_rationale,
            )
        except ValidationError as exc:
            raise InvalidRequest("Complete the determination reason, approved units, duration, and any override rationale.") from exc
        if adverse and current.case.status != Status.IN_PHYSICIAN_REVIEW:
            self.workflow.transition(
                case_id, Status.IN_PHYSICIAN_REVIEW, actor, "Licensed reviewer assumed the adverse determination review.",
            )
        current = self.workflow.transition(
            case_id, Status.DETERMINED, actor, "Authenticated clinician rendered a determination.",
            {"determination": determination},
        )
        self.workflow.record(
            case_id, actor, "human_determination", "Clinical determination and override recorded.",
            determination.model_dump(mode="json"),
        )
        self.workflow.transition(
            case_id, Status.NOTIFICATION_PENDING_APPROVAL, SYSTEM,
            "Determination letters require a separate human release.",
        )
        return asyncio.run(self.draft_letters(case_id))

    def accept(self, case_id: UUID, actor: Actor) -> CaseAggregate:
        current = self._clinical(case_id, actor)
        if current.recommendation is None or current.recommendation.outcome.value != "APPROVE_RECOMMENDED":
            raise Conflict("There is no approval recommendation to accept.")
        return self.determine(case_id, actor, DeterminationRequest(
            outcome=DeterminationOutcome.APPROVED,
            approved_quantity=current.case.service.quantity,
            approved_duration_days=current.case.service.days_supply or 1,
        ))

    async def draft_letters(self, case_id: UUID) -> CaseAggregate:
        current = self.store.get(case_id)
        expected = draft_text(current, self.config)
        result = await self.service.runner.run(
            LETTER, case_id, expected.model_dump(mode="json"),
            current.criteria_snapshot.version if current.criteria_snapshot else None,
        )
        self.service.record_run(result)
        if result.output is None or result.output != expected:
            reason = result.run.error or "The letter proposal changed required template facts. Human drafting is required."
            return self.workflow.record(
                case_id, SYSTEM, "letter_draft_failed", reason, {"failure": reason},
                {"failures": (*current.failures, reason)},
            )
        letters = (
            Letter(recipient="member", text=expected.member_text),
            Letter(recipient="provider", text=expected.provider_text),
        )
        return self.workflow.record(
            case_id, SYSTEM, "letters_drafted", "Letter drafts await human review.",
            {"letter_ids": [str(letter.letter_id) for letter in letters]}, {"letters": letters},
        )

    def manual_draft(self, case_id: UUID, actor: Actor) -> CaseAggregate:
        require_roles(actor, RELEASE_ROLES)
        current = self.store.get(case_id)
        if current.case.status != Status.NOTIFICATION_PENDING_APPROVAL:
            raise Conflict("Letters can only be drafted while awaiting release.")
        text = draft_text(current, self.config)
        letters = (
            Letter(recipient="member", text=text.member_text),
            Letter(recipient="provider", text=text.provider_text),
        )
        return self.workflow.record(
            case_id, actor, "human_letter_draft", "Human selected the deterministic letter templates.",
            {"letter_ids": [str(letter.letter_id) for letter in letters]}, {"letters": letters},
        )

    def release(self, case_id: UUID, actor: Actor, request: LetterRelease) -> CaseAggregate:
        require_roles(actor, RELEASE_ROLES)
        current = self.store.get(case_id)
        if current.case.status != Status.NOTIFICATION_PENDING_APPROVAL or not current.letters:
            raise Conflict("Letter drafts are not ready for release.")
        drafts = {letter.recipient: letter.text for letter in current.letters}
        member = request.member_text or drafts["member"]
        provider = request.provider_text or drafts["provider"]
        validate_release_text(member, provider, current, self.config)
        now = datetime.now(UTC)
        letters = tuple(letter.model_copy(update={
            "text": member if letter.recipient == "member" else provider,
            "status": "RELEASED", "approved_by": actor.actor_id, "released_at": now,
            "sha256": hashlib.sha256(
                (member if letter.recipient == "member" else provider).encode()
            ).hexdigest(),
        }) for letter in current.letters)
        self.workflow.transition(
            case_id, Status.NOTIFIED, actor,
            "A human approved and released both letters to the local delivery outbox.",
            {"letters": letters}, now=now,
        )
        self.workflow.record(
            case_id, actor, "letters_released", "Exact released letter text and hashes recorded.",
            {"letters": [letter.model_dump(mode="json") for letter in letters]},
        )
        return self.workflow.transition(
            case_id, Status.CLOSED, SYSTEM, "Human approved notification completed.", now=now,
        )

    def request_info(
        self, case_id: UUID, actor: Actor, questions: tuple[DTRQuestion, ...],
    ) -> CaseAggregate:
        current = self._clinical(case_id, actor)
        valid = {c.id for c in current.criteria_snapshot.criteria} if current.criteria_snapshot else set()
        if any(q.criterion_id not in valid | {"additional_information"} for q in questions):
            raise InvalidRequest("Information questions reference an unknown criterion.")
        now = datetime.now(UTC)
        request = InformationRequest(
            questions=questions, requested_by=actor.actor_id, requested_at=now,
        )
        result = self.workflow.transition(
            case_id, Status.PEND_MISSING_INFO, actor,
            "Human approved the information request. Delivery is recorded locally.",
            {"information_requests": (*current.information_requests, request)}, now=now,
        )
        self.workflow.record(
            case_id, actor, "information_requested", "Structured information request recorded.",
            request.model_dump(mode="json"),
        )
        return result

    def receive_info(self, case_id: UUID, actor: Actor, sources: tuple[Source, ...]) -> CaseAggregate:
        require_roles(actor, INTAKE_ROLES)
        current = self.store.get(case_id)
        identities = [source.source_id for source in (*current.sources, *sources)]
        if len(set(identities)) != len(identities):
            raise InvalidRequest("New information must have new immutable source identifiers.")
        now = datetime.now(UTC)
        requests = tuple(
            item.model_copy(update={"received_at": now}) if item.received_at is None else item
            for item in current.information_requests
        )
        self.workflow.transition(
            case_id, Status.DOCS_CHECK, actor, "Additional information received.",
            {
                "sources": (*current.sources, *sources), "information_requests": requests,
                "evidence": (), "contradictions": (), "evaluation": None,
                "recommendation": None, "documentation": None, "reviewer_summary": None,
                "failures": (),
            }, now=now,
        )
        return asyncio.run(self.service.analyze_evidence(case_id))

    def escalate(self, case_id: UUID, actor: Actor, reason: str) -> CaseAggregate:
        self._clinical(case_id, actor)
        return self.workflow.transition(
            case_id, Status.IN_PHYSICIAN_REVIEW, actor, reason,
        )

    def schedule_peer(
        self, case_id: UUID, actor: Actor, scheduled_at: datetime, reason: str,
    ) -> CaseAggregate:
        self._clinical(case_id, actor)
        if scheduled_at <= datetime.now(UTC):
            raise InvalidRequest("Peer to peer scheduling requires a future time.")
        return self.workflow.transition(
            case_id, Status.PEER_TO_PEER_SCHEDULED, actor, reason,
            {"peer_to_peer_at": scheduled_at},
        )

    def withdraw(self, case_id: UUID, actor: Actor, reason: str) -> CaseAggregate:
        require_roles(actor, INTAKE_ROLES | {Role.SUPERVISOR})
        self.workflow.transition(case_id, Status.WITHDRAWN, actor, reason)
        return self.workflow.transition(case_id, Status.CLOSED, SYSTEM, "Withdrawal completed.")

    async def copilot(self, case_id: UUID, actor: Actor, question: str) -> SummaryOutput:
        current = self._clinical(case_id, actor)
        evidence = tuple(item for item in current.evidence if item.verified)
        result = await self.service.runner.run(
            COPILOT, case_id, {
                "question": question,
                "evidence": [item.model_dump(mode="json") for item in evidence],
            },
            current.criteria_snapshot.version if current.criteria_snapshot else None,
        )
        self.service.record_run(result)
        if result.output is None:
            raise AgentFailure(result.run.error or "Reviewer assistance failed.")
        allowed = {item.evidence_id: item for item in evidence}
        if not set(result.output.evidence_ids) <= set(allowed):
            raise InvalidRequest("The reviewer assistance proposal contains unsupported citations.")
        selected = tuple(allowed[identity] for identity in result.output.evidence_ids)
        answer = " ".join(
            f"{item.fact_type}: {canonical_json(item.normalized_value)}"
            f"{' ' + item.unit if item.unit else ''}. Source: {item.citation.source_id}."
            for item in selected
        ) or "No verified evidence supports an answer to this question."
        return SummaryOutput(summary=answer, evidence_ids=result.output.evidence_ids)

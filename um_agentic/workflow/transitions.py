from collections.abc import Callable
from dataclasses import dataclass

from um_agentic.contracts import (
    Actor,
    ActorKind,
    CaseAggregate,
    CriterionStatus,
    DeterminationOutcome,
    LineOfBusiness,
    Role,
    Status,
)
from um_agentic.errors import Forbidden, InvalidRequest
from um_agentic.settings import Configuration


@dataclass(frozen=True)
class TransitionContext:
    aggregate: CaseAggregate
    actor: Actor
    config: Configuration


def system_only(ctx: TransitionContext) -> None:
    if ctx.actor.kind != ActorKind.SYSTEM:
        raise Forbidden("Only the workflow can commit this transition.")


def human_only(ctx: TransitionContext) -> None:
    if ctx.actor.kind != ActorKind.HUMAN:
        raise Forbidden("An authenticated human action is required.")


def clinical_human(ctx: TransitionContext) -> None:
    human_only(ctx)
    roles = ctx.actor.roles & {Role.NURSE, Role.PHYSICIAN, Role.PHARMACIST}
    if not roles:
        raise Forbidden("A clinical reviewer credential is required.")
    if roles == {Role.PHARMACIST} and ctx.aggregate.case.line_of_business != LineOfBusiness.PHARMACY:
        raise Forbidden("A pharmacist may review pharmacy cases only.")


def physician_human(ctx: TransitionContext) -> None:
    clinical_human(ctx)
    licensed = ctx.actor.roles & ctx.config.adverse_reviewer_roles
    if not licensed:
        raise Forbidden("A configured licensed reviewer is required.")
    if licensed == {Role.PHARMACIST} and ctx.aggregate.case.line_of_business != LineOfBusiness.PHARMACY:
        raise Forbidden("A pharmacist may review pharmacy cases only.")


def determination_guard(ctx: TransitionContext) -> None:
    clinical_human(ctx)
    decision = ctx.aggregate.determination
    if decision is None:
        raise InvalidRequest("A validated determination is required.")
    if decision.reviewer_id != ctx.actor.actor_id or decision.reviewer_role not in ctx.actor.roles:
        raise Forbidden("The determination must belong to the authenticated reviewer.")
    if decision.outcome != DeterminationOutcome.APPROVED:
        physician_human(ctx)
        if ctx.aggregate.case.status != Status.IN_PHYSICIAN_REVIEW:
            raise Forbidden("An adverse determination requires licensed review state.")


def auto_approval_guard(ctx: TransitionContext) -> None:
    system_only(ctx)
    data = ctx.aggregate
    criteria = data.criteria_snapshot
    evaluation = data.evaluation
    if not ctx.config.auto_approve_enabled[data.case.line_of_business]:
        raise Forbidden("Automatic approval is disabled for this line of business.")
    if (
        criteria is None
        or not criteria.auto_approve_eligible
        or any(c.requires_clinical_judgment for c in criteria.criteria)
        or evaluation is None
        or evaluation.overall != "ALL_MET"
        or evaluation.criteria_set_id != criteria.criteria_set_id
        or evaluation.version != criteria.version
        or {r.criterion_id for r in evaluation.results} != {c.id for c in criteria.criteria}
        or data.contradictions
        or data.failures
    ):
        raise Forbidden("Deterministic criteria do not permit automatic approval.")
    verified = {e.evidence_id for e in data.evidence if e.verified}
    if any(
        r.status != CriterionStatus.MET or not r.evidence_ids or not set(r.evidence_ids) <= verified
        for r in evaluation.results
    ):
        raise Forbidden("Every automatic approval criterion requires verified evidence.")


def release_guard(ctx: TransitionContext) -> None:
    human_only(ctx)
    if not ctx.actor.roles & {Role.NURSE, Role.PHYSICIAN, Role.PHARMACIST, Role.SUPERVISOR}:
        raise Forbidden("A letter release role is required.")
    if not ctx.aggregate.letters or any(
        letter.status != "RELEASED" or letter.approved_by != ctx.actor.actor_id
        for letter in ctx.aggregate.letters
    ):
        raise Forbidden("Every letter must have human release approval.")


Guard = Callable[[TransitionContext], None]

TRANSITIONS: dict[Status, dict[Status, Guard]] = {
    Status.RECEIVED: {Status.NORMALIZED: system_only, Status.WITHDRAWN: human_only},
    Status.NORMALIZED: {Status.ADMIN_CHECK: system_only, Status.WITHDRAWN: human_only},
    Status.ADMIN_CHECK: {
        Status.NO_PA_REQUIRED: system_only,
        Status.ADMIN_PEND: system_only,
        Status.DOCS_CHECK: system_only,
        Status.WITHDRAWN: human_only,
    },
    Status.NO_PA_REQUIRED: {Status.CLOSED: system_only},
    Status.ADMIN_PEND: {Status.ADMIN_CHECK: human_only, Status.WITHDRAWN: human_only},
    Status.DOCS_CHECK: {
        Status.CLINICAL_ANALYSIS: system_only,
        Status.IN_CLINICAL_REVIEW: system_only,
        Status.WITHDRAWN: human_only,
    },
    Status.CLINICAL_ANALYSIS: {
        Status.VERIFICATION: system_only,
        Status.IN_CLINICAL_REVIEW: system_only,
        Status.WITHDRAWN: human_only,
    },
    Status.VERIFICATION: {
        Status.RECOMMENDED: system_only,
        Status.IN_CLINICAL_REVIEW: system_only,
        Status.WITHDRAWN: human_only,
    },
    Status.RECOMMENDED: {
        Status.AUTO_APPROVED: auto_approval_guard,
        Status.IN_CLINICAL_REVIEW: system_only,
        Status.IN_PHYSICIAN_REVIEW: system_only,
        Status.WITHDRAWN: human_only,
    },
    Status.AUTO_APPROVED: {Status.NOTIFICATION_PENDING_APPROVAL: system_only},
    Status.IN_CLINICAL_REVIEW: {
        Status.IN_PHYSICIAN_REVIEW: clinical_human,
        Status.PEND_MISSING_INFO: clinical_human,
        Status.DETERMINED: determination_guard,
        Status.WITHDRAWN: human_only,
    },
    Status.PEND_MISSING_INFO: {Status.DOCS_CHECK: human_only, Status.WITHDRAWN: human_only},
    Status.IN_PHYSICIAN_REVIEW: {
        Status.PEER_TO_PEER_SCHEDULED: physician_human,
        Status.PEND_MISSING_INFO: clinical_human,
        Status.DETERMINED: determination_guard,
        Status.WITHDRAWN: human_only,
    },
    Status.PEER_TO_PEER_SCHEDULED: {
        Status.IN_PHYSICIAN_REVIEW: physician_human,
        Status.WITHDRAWN: human_only,
    },
    Status.DETERMINED: {Status.NOTIFICATION_PENDING_APPROVAL: system_only},
    Status.NOTIFICATION_PENDING_APPROVAL: {Status.NOTIFIED: release_guard},
    Status.NOTIFIED: {Status.CLOSED: system_only},
    Status.WITHDRAWN: {Status.CLOSED: system_only},
    Status.CLOSED: {},
}

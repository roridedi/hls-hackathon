from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from um_agentic.contracts import Actor, ActorKind, AgentRun, CaseAggregate, Status
from um_agentic.errors import Conflict, Forbidden, InvalidRequest
from um_agentic.persistence import AgentRunRow, CaseRow, IdempotencyRow, Store
from um_agentic.settings import Configuration
from um_agentic.workflow.clocks import pause_clock, refresh_clock, resume_clock
from um_agentic.workflow.transitions import TRANSITIONS, TransitionContext


class Workflow:
    def __init__(self, store: Store, config: Configuration) -> None:
        self.store = store
        self.config = config

    def receive(
        self,
        aggregate: CaseAggregate,
        actor: Actor,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> CaseAggregate:
        if aggregate.case.status != Status.RECEIVED or actor.kind == ActorKind.AGENT:
            raise Forbidden("Only intake may create a received case.")
        try:
            with self.store.transaction() as session:
                session.add(CaseRow(
                    case_id=str(aggregate.case.case_id),
                    status=Status.RECEIVED.value,
                    version=1,
                    data=aggregate.model_dump(mode="json"),
                ))
                session.flush()
                self.store.append_event(
                    session, aggregate, 1, "case_received", actor,
                    "Request received.", {
                        "channel": aggregate.case.channel.value,
                        "line_of_business": aggregate.case.line_of_business.value,
                        "urgency": aggregate.case.urgency.value,
                        "received_at": aggregate.case.received_at.isoformat(),
                        "due_at": aggregate.case.clock.due_at.isoformat(),
                        "service": aggregate.case.service.model_dump(mode="json"),
                        "provider_npi": aggregate.case.requesting_provider.npi,
                    },
                    aggregate.case.received_at,
                )
                if idempotency_key is not None:
                    if request_hash is None:
                        raise InvalidRequest("An idempotent request requires a hash.")
                    session.add(IdempotencyRow(
                        key=idempotency_key, request_hash=request_hash,
                        case_id=str(aggregate.case.case_id),
                    ))
        except IntegrityError as exc:
            raise Conflict("The case or idempotency key already exists.") from exc
        return aggregate

    def transition(
        self,
        case_id: UUID,
        target: Status,
        actor: Actor,
        reason: str,
        updates: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> CaseAggregate:
        if actor.kind == ActorKind.AGENT:
            raise Forbidden("Agents can propose outputs but cannot change case state.")
        if not reason.strip():
            raise InvalidRequest("Every transition requires a reason.")
        if updates and "case" in updates:
            raise InvalidRequest("Case state can only change through the transition table.")
        when = now or datetime.now(UTC)
        with self.store.transaction() as session:
            row = self.store.locked(session, case_id)
            current = CaseAggregate.model_validate(row.data)
            guard = TRANSITIONS[current.case.status].get(target)
            if guard is None:
                raise Conflict(f"Transition from {current.case.status} to {target} is not allowed.")
            proposed = CaseAggregate.model_validate({
                **current.model_dump(), **(updates or {}),
            })
            guard(TransitionContext(proposed, actor, self.config))
            new_case = proposed.case.model_dump()
            new_case["status"] = target
            if target == Status.PEND_MISSING_INFO:
                new_case["clock"] = pause_clock(
                    proposed.case, when,
                    self.config.clock_policy(
                        proposed.case.line_of_business, proposed.case.jurisdiction
                    ),
                )
            elif current.case.status == Status.PEND_MISSING_INFO and target == Status.DOCS_CHECK:
                new_case["clock"] = resume_clock(proposed.case, when)
            if target == Status.CLOSED:
                proposed = CaseAggregate.model_validate({
                    **proposed.model_dump(), "closed_at": when,
                })
            proposed = CaseAggregate.model_validate({
                **proposed.model_dump(), "case": new_case,
            })
            self.store.append_event(
                session, proposed, row.version + 1, "state_transition", actor, reason,
                {
                    "from": current.case.status.value, "to": target.value,
                    "updates": {
                        key: proposed.model_dump(mode="json")[key] for key in (updates or {})
                    },
                    "clock": proposed.case.clock.model_dump(mode="json"),
                }, when,
            )
            self.store.save(session, row, proposed, row.version)
        return proposed

    def record(
        self,
        case_id: UUID,
        actor: Actor,
        event_type: str,
        reason: str,
        payload: dict[str, Any],
        updates: dict[str, Any] | None = None,
        run: AgentRun | None = None,
        now: datetime | None = None,
    ) -> CaseAggregate:
        if actor.kind == ActorKind.AGENT:
            raise Forbidden("Agents have no case write tools.")
        if updates and "case" in updates:
            raise InvalidRequest("Case state is owned by the workflow.")
        with self.store.transaction() as session:
            row = self.store.locked(session, case_id)
            current = CaseAggregate.model_validate(row.data)
            proposed = CaseAggregate.model_validate({
                **current.model_dump(), **(updates or {}),
            })
            if run is not None:
                if run.case_id != case_id:
                    raise InvalidRequest("Agent run belongs to a different case.")
                session.add(AgentRunRow(
                    run_id=str(run.run_id), case_id=str(case_id),
                    data=run.model_dump(mode="json"),
                ))
                proposed = CaseAggregate.model_validate({
                    **proposed.model_dump(),
                    "agent_run_ids": (*proposed.agent_run_ids, run.run_id),
                })
            self.store.append_event(
                session, proposed, row.version + 1, event_type, actor, reason,
                payload, now or datetime.now(UTC),
            )
            self.store.save(session, row, proposed, row.version)
        return proposed

    def tick(self, case_id: UUID, now: datetime) -> CaseAggregate:
        from um_agentic.contracts import SYSTEM

        with self.store.transaction() as session:
            row = self.store.locked(session, case_id)
            current = CaseAggregate.model_validate(row.data)
            if current.case.status in {Status.CLOSED, Status.WITHDRAWN, Status.NOTIFIED}:
                return current
            clock = refresh_clock(current.case, now)
            policy = self.config.clock_policy(
                current.case.line_of_business, current.case.jurisdiction
            )
            warn = clock.percent_elapsed >= policy.warning_fraction * 100
            first_warning = warn and not clock.warning_emitted
            clock = clock.model_copy(update={"warning_emitted": clock.warning_emitted or warn})
            proposed = CaseAggregate.model_validate({
                **current.model_dump(),
                "case": {**current.case.model_dump(), "clock": clock},
            })
            self.store.append_event(
                session, proposed, row.version + 1,
                "clock_warning" if first_warning else "clock_updated", SYSTEM,
                "Regulatory clock evaluated.",
                {"percent_elapsed": clock.percent_elapsed, "due_at": clock.due_at.isoformat()},
                now,
            )
            self.store.save(session, row, proposed, row.version)
        return proposed

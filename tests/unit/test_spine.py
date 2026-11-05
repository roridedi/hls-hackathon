import json
from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from um_agentic.contracts import (
    AGENT_OUTPUT_MODELS,
    SYSTEM,
    Actor,
    ActorKind,
    CaseAggregate,
    Determination,
    DeterminationOutcome,
    Role,
    Status,
)
from um_agentic.errors import Conflict, Forbidden, InvalidRequest
from um_agentic.persistence import Store
from um_agentic.serialization import content_hash
from um_agentic.settings import Configuration, Settings
from um_agentic.workflow import Workflow
from um_agentic.workflow.clocks import pause_clock, refresh_clock, resume_clock
from um_agentic.workflow.transitions import TRANSITIONS


def test_contracts_reject_unknown_fields_and_naive_time(aggregate: CaseAggregate) -> None:
    with pytest.raises(ValidationError):
        type(aggregate.case).model_validate({**aggregate.case.model_dump(), "deny": True})
    with pytest.raises(ValidationError):
        type(aggregate.case).model_validate({
            **aggregate.case.model_dump(), "received_at": "2026-10-30T21:00:00",
        })
    with pytest.raises(ValidationError):
        aggregate.case.status = Status.CLOSED  # type: ignore[misc]


def test_no_agent_output_schema_can_express_adverse_outcome() -> None:
    for model in AGENT_OUTPUT_MODELS:
        schema = json.dumps(model.model_json_schema())
        assert '"DENIED"' not in schema
        assert '"DENY"' not in schema
        assert '"PARTIALLY_APPROVED"' not in schema
        with pytest.raises(ValidationError):
            model.model_validate({"outcome": "DENIED"})


def test_determination_requires_reason_and_override_rationale() -> None:
    with pytest.raises(ValidationError):
        Determination(
            outcome=DeterminationOutcome.DENIED,
            reviewer_id="reviewer", reviewer_role=Role.PHYSICIAN,
        )


def test_every_state_has_one_transition_table_entry() -> None:
    assert set(TRANSITIONS) == set(Status)
    assert TRANSITIONS[Status.CLOSED] == {}


@given(st.sampled_from(list(Status)), st.sampled_from(list(Status)))
@hypothesis_settings(max_examples=100)
def test_agent_cannot_transition_any_pair(source: Status, target: Status) -> None:
    configuration = Settings().configuration()
    store = Store("sqlite+pysqlite:///:memory:")
    workflow = Workflow(store, configuration)
    agent = Actor(actor_id="malicious-agent", kind=ActorKind.AGENT, roles={Role.PHYSICIAN})
    from uuid import uuid4
    with pytest.raises(Forbidden):
        workflow.transition(uuid4(), target, agent, f"Attempt from {source}.")
    store.engine.dispose()


def test_illegal_transition_and_event_atomicity(
    workflow: Workflow, aggregate: CaseAggregate, intake_actor: Actor, store: Store
) -> None:
    workflow.receive(aggregate, intake_actor)
    with pytest.raises(Conflict):
        workflow.transition(aggregate.case.case_id, Status.CLOSED, SYSTEM, "Invalid shortcut.")
    assert store.get(aggregate.case.case_id).case.status == Status.RECEIVED
    assert len(store.events(aggregate.case.case_id)) == 1
    workflow.transition(aggregate.case.case_id, Status.NORMALIZED, SYSTEM, "Normalized.")
    assert store.verify_chain(aggregate.case.case_id)
    with store.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM outbox")) == 2


def test_event_log_is_append_only(
    workflow: Workflow, aggregate: CaseAggregate, intake_actor: Actor, store: Store
) -> None:
    workflow.receive(aggregate, intake_actor)
    for sql in ("UPDATE events SET sequence = 50", "DELETE FROM events"):
        with pytest.raises(DatabaseError), store.engine.begin() as connection:
            connection.execute(text(sql))
    assert store.verify_chain(aggregate.case.case_id)


def test_idempotency_hash_and_no_status_patch(
    workflow: Workflow, aggregate: CaseAggregate, intake_actor: Actor, store: Store
) -> None:
    digest = content_hash(aggregate.original_request)
    workflow.receive(aggregate, intake_actor, "synthetic-request", digest)
    assert store.find_idempotent("synthetic-request", digest) == aggregate.case.case_id
    with pytest.raises(Conflict):
        store.find_idempotent("synthetic-request", "different")
    with pytest.raises(InvalidRequest):
        workflow.record(
            aggregate.case.case_id, SYSTEM, "tamper", "Invalid patch.", {},
            {"case": {"status": "DETERMINED"}},
        )


def test_default_clock_does_not_pause(
    aggregate: CaseAggregate, config: Configuration
) -> None:
    later = aggregate.case.received_at + timedelta(hours=12)
    policy = config.clock_policy(aggregate.case.line_of_business, "DEFAULT")
    clock = pause_clock(aggregate.case, later, policy)
    assert not clock.paused
    assert clock.percent_elapsed == pytest.approx(100 / 6)


def test_configured_pause_extends_due_time(
    aggregate: CaseAggregate, config: Configuration
) -> None:
    policy = config.clock_policy(aggregate.case.line_of_business, "DEFAULT")
    policy = policy.model_copy(update={"pause_for_missing_info": True})
    start = aggregate.case.received_at + timedelta(hours=12)
    paused = pause_clock(aggregate.case, start, policy)
    case = aggregate.case.model_copy(update={"clock": paused})
    assert refresh_clock(case, start + timedelta(days=3)).percent_elapsed == paused.percent_elapsed
    resumed = resume_clock(case, start + timedelta(days=3))
    assert resumed.due_at == aggregate.case.clock.due_at + timedelta(days=3)
    assert not resumed.paused


def test_production_cannot_use_dev_auth() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production")

from datetime import UTC, datetime, timedelta

import pytest

from um_agentic.contracts import Actor, CaseAggregate
from um_agentic.persistence import Store
from um_agentic.workflow import Workflow


def test_g9_expedited_friday_clock_includes_weekend_and_warns_once(
    workflow: Workflow, aggregate: CaseAggregate, intake_actor: Actor, store: Store
) -> None:
    assert aggregate.case.received_at.weekday() == 4
    assert aggregate.case.clock.due_at == datetime(2026, 11, 2, 21, 0, tzinfo=UTC)
    workflow.receive(aggregate, intake_actor)
    workflow.tick(aggregate.case.case_id, aggregate.case.received_at + timedelta(hours=53))
    assert not any(e.event_type == "clock_warning" for e in store.events(aggregate.case.case_id))
    warned = workflow.tick(
        aggregate.case.case_id, aggregate.case.received_at + timedelta(hours=54)
    )
    assert warned.case.clock.percent_elapsed == pytest.approx(75)
    workflow.tick(aggregate.case.case_id, aggregate.case.received_at + timedelta(hours=60))
    assert sum(e.event_type == "clock_warning" for e in store.events(aggregate.case.case_id)) == 1

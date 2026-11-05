import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from um_agentic.contracts import SYSTEM, Actor, CaseAggregate, Status
from um_agentic.persistence import Store
from um_agentic.settings import Configuration
from um_agentic.workflow import Workflow


@pytest.mark.postgres
def test_postgres_transaction_outbox_and_immutable_audit(
    postgres_store: Store, config: Configuration, aggregate: CaseAggregate, intake_actor: Actor
) -> None:
    workflow = Workflow(postgres_store, config)
    workflow.receive(aggregate, intake_actor)
    workflow.transition(aggregate.case.case_id, Status.NORMALIZED, SYSTEM, "Normalization complete.")
    assert postgres_store.verify_chain(aggregate.case.case_id)
    with pytest.raises(DatabaseError), postgres_store.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM events WHERE case_id = :case_id"),
            {"case_id": str(aggregate.case.case_id)},
        )

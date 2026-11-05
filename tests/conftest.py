import os
import socket
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest

from um_agentic.contracts import (
    Actor,
    ActorKind,
    CaseAggregate,
    Channel,
    LineOfBusiness,
    Provider,
    Role,
    Service,
    UMCase,
    Urgency,
)
from um_agentic.persistence import Store
from um_agentic.settings import Configuration, Settings
from um_agentic.workflow import Workflow
from um_agentic.workflow.clocks import start_clock


@pytest.fixture(autouse=True)
def offline_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("postgres"):
        return

    def blocked(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Default tests must not use a network.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def settings(tmp_path: Any) -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite+pysqlite:///:memory:",
        storage_path=tmp_path,
    )


@pytest.fixture
def config(settings: Settings) -> Configuration:
    return settings.configuration()


@pytest.fixture
def store(settings: Settings) -> Iterator[Store]:
    repository = Store(settings.database_url)
    repository.initialize()
    yield repository
    repository.engine.dispose()


@pytest.fixture
def workflow(store: Store, config: Configuration) -> Workflow:
    return Workflow(store, config)


@pytest.fixture
def intake_actor() -> Actor:
    return Actor(actor_id="synthetic-intake", kind=ActorKind.HUMAN, roles={Role.INTAKE})


@pytest.fixture
def physician() -> Actor:
    return Actor(actor_id="synthetic-physician", kind=ActorKind.HUMAN, roles={Role.PHYSICIAN})


@pytest.fixture
def aggregate(config: Configuration) -> CaseAggregate:
    received = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
    case = UMCase(
        channel=Channel.FHIR,
        line_of_business=LineOfBusiness.PHARMACY,
        urgency=Urgency.EXPEDITED,
        received_at=received,
        member_token="member_000001",
        date_of_service=date(2026, 11, 1),
        requesting_provider=Provider(npi="1234567893", specialty="Primary care"),
        service=Service(code_system="RXNORM_CLASS", code="GLP1"),
        diagnoses=("E11.9",),
        clock=start_clock(
            received, Urgency.EXPEDITED,
            config.clock_policy(LineOfBusiness.PHARMACY, "DEFAULT"),
        ),
    )
    return CaseAggregate(case=case, original_request={"synthetic": True})


@pytest.fixture
def postgres_store() -> Iterator[Store]:
    url = os.environ.get("UM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set UM_TEST_DATABASE_URL for PostgreSQL acceptance tests.")
    repository = Store(url)
    repository.initialize()
    yield repository
    repository.engine.dispose()

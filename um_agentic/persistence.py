from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from um_agentic.contracts import Actor, AgentRun, CaseAggregate, Event
from um_agentic.errors import Conflict, NotFound
from um_agentic.serialization import content_hash


class Base(DeclarativeBase):
    pass


class CaseRow(Base):
    __tablename__ = "cases"
    case_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(50), index=True)
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("case_id", "sequence"),)
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    event_hash: Mapped[str] = mapped_column(String(64))


class OutboxRow(Base):
    __tablename__ = "outbox"
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id"), primary_key=True)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentRunRow(Base):
    __tablename__ = "agent_runs"
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.case_id"), index=True, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class IdempotencyRow(Base):
    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"))


def make_engine(url: str, token_provider: Callable[[], str] | None = None) -> Engine:
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    if token_provider is not None:
        @event.listens_for(engine, "do_connect")
        def inject_token(dialect: Any, record: Any, args: Any, parameters: Any) -> None:
            parameters["password"] = token_provider()
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def foreign_keys(connection: Any, record: Any) -> None:
            connection.execute("PRAGMA foreign_keys=ON")
    return engine


class Store:
    def __init__(
        self, database_url: str, token_provider: Callable[[], str] | None = None,
    ) -> None:
        self.engine = make_engine(database_url, token_provider)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            if self.engine.dialect.name == "sqlite":
                for table in ("events", "agent_runs"):
                    for operation in ("UPDATE", "DELETE"):
                        connection.execute(text(
                            f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation.lower()} "
                            f"BEFORE {operation} ON {table} "
                            "BEGIN SELECT RAISE(ABORT, 'Audit records are append only.'); END"
                        ))
            elif self.engine.dialect.name == "postgresql":
                connection.execute(text(
                    "CREATE OR REPLACE FUNCTION um_reject_audit_mutation() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ BEGIN "
                    "RAISE EXCEPTION 'Audit records are append only.'; END; $$"
                ))
                for table in ("events", "agent_runs"):
                    connection.execute(text(f"DROP TRIGGER IF EXISTS immutable_audit ON {table}"))
                    connection.execute(text(
                        f"CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON {table} "
                        "FOR EACH ROW EXECUTE FUNCTION um_reject_audit_mutation()"
                    ))

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with self.sessions.begin() as session:
            yield session

    def locked(self, session: Session, case_id: UUID) -> CaseRow:
        row = session.scalar(
            select(CaseRow).where(CaseRow.case_id == str(case_id)).with_for_update()
        )
        if row is None:
            raise NotFound("Case not found.")
        return row

    def get(self, case_id: UUID) -> CaseAggregate:
        with self.sessions() as session:
            row = session.get(CaseRow, str(case_id))
            if row is None:
                raise NotFound("Case not found.")
            return CaseAggregate.model_validate(row.data)

    def all_cases(self) -> list[CaseAggregate]:
        with self.sessions() as session:
            return [
                CaseAggregate.model_validate(row.data)
                for row in session.scalars(select(CaseRow))
            ]

    def events(self, case_id: UUID | None = None) -> list[Event]:
        with self.sessions() as session:
            query = select(EventRow).order_by(EventRow.case_id, EventRow.sequence)
            if case_id is not None:
                query = query.where(EventRow.case_id == str(case_id))
            return [Event.model_validate(row.data) for row in session.scalars(query)]

    def runs(self, case_id: UUID) -> list[AgentRun]:
        with self.sessions() as session:
            runs = [
                AgentRun.model_validate(row.data)
                for row in session.scalars(
                    select(AgentRunRow).where(AgentRunRow.case_id == str(case_id))
                )
            ]
            order = {identity: index for index, identity in enumerate(self.get(case_id).agent_run_ids)}
            return sorted(runs, key=lambda run: order.get(run.run_id, len(order)))

    def save_program_run(self, run: AgentRun) -> None:
        if run.case_id is not None:
            raise Conflict("Program runs must not impersonate a case.")
        with self.transaction() as session:
            session.add(AgentRunRow(run_id=str(run.run_id), case_id=None, data=run.model_dump(mode="json")))

    def find_idempotent(self, key: str, request_hash: str) -> UUID | None:
        with self.sessions() as session:
            row = session.get(IdempotencyRow, key)
            if row is None:
                return None
            if row.request_hash != request_hash:
                raise Conflict("The idempotency key was already used for a different request.")
            return UUID(row.case_id)

    def append_event(
        self,
        session: Session,
        aggregate: CaseAggregate,
        sequence: int,
        event_type: str,
        actor: Actor,
        reason: str,
        payload: dict[str, Any],
        occurred_at: datetime,
    ) -> Event:
        payload = {
            **payload, "aggregate_hash": content_hash(aggregate.model_dump(mode="json")),
        }
        previous = session.scalar(
            select(EventRow)
            .where(EventRow.case_id == str(aggregate.case.case_id))
            .order_by(EventRow.sequence.desc())
            .limit(1)
        )
        body = {
            "event_id": str(uuid4()),
            "case_id": str(aggregate.case.case_id),
            "sequence": sequence,
            "event_type": event_type,
            "actor": actor.model_dump(mode="json"),
            "occurred_at": occurred_at.astimezone(UTC).isoformat(),
            "reason": reason,
            "payload": payload,
            "payload_hash": content_hash(payload),
            "previous_hash": previous.event_hash if previous else "0" * 64,
        }
        record = Event.model_validate({**body, "event_hash": "0" * 64})
        record = record.model_copy(update={
            "event_hash": content_hash(record.model_dump(mode="json", exclude={"event_hash"})),
        })
        session.add(EventRow(
            event_id=str(record.event_id),
            case_id=str(record.case_id),
            sequence=sequence,
            data=record.model_dump(mode="json"),
            event_hash=record.event_hash,
        ))
        session.flush()
        session.add(OutboxRow(event_id=str(record.event_id)))
        return record

    def save(
        self, session: Session, row: CaseRow, aggregate: CaseAggregate, expected_version: int
    ) -> None:
        changed = session.scalar(
            update(CaseRow)
            .where(CaseRow.case_id == row.case_id, CaseRow.version == expected_version)
            .values(
                status=aggregate.case.status.value,
                data=aggregate.model_dump(mode="json"),
                version=expected_version + 1,
            )
            .returning(CaseRow.case_id)
            .execution_options(synchronize_session=False)
        )
        if changed is None:
            raise Conflict("The case changed. Refresh before trying again.")

    def verify_chain(self, case_id: UUID) -> bool:
        previous = "0" * 64
        records = self.events(case_id)
        for index, record in enumerate(records, start=1):
            body = record.model_dump(mode="json", exclude={"event_hash"})
            if (
                record.sequence != index
                or record.previous_hash != previous
                or record.payload_hash != content_hash(record.payload)
                or record.event_hash != content_hash(body)
            ):
                return False
            previous = record.event_hash
        return bool(records) and records[-1].payload.get("aggregate_hash") == content_hash(
            self.get(case_id).model_dump(mode="json")
        )

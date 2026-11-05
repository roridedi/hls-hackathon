from datetime import UTC, datetime, timedelta

from um_agentic.contracts import Clock, UMCase, Urgency
from um_agentic.errors import InvalidRequest
from um_agentic.settings import ClockPolicy


def start_clock(received_at: datetime, urgency: Urgency, policy: ClockPolicy) -> Clock:
    if received_at.tzinfo is None:
        raise InvalidRequest("Receipt time requires a timezone.")
    hours = policy.expedited_hours if urgency == Urgency.EXPEDITED else policy.standard_hours
    return Clock(due_at=received_at.astimezone(UTC) + timedelta(hours=hours), sla_hours=hours)


def refresh_clock(case: UMCase, now: datetime) -> Clock:
    if now.tzinfo is None:
        raise InvalidRequest("Clock evaluation requires a timezone.")
    stop = case.clock.pause_started_at if case.clock.paused else now
    assert stop is not None
    elapsed = (stop - case.received_at).total_seconds() - case.clock.total_paused_seconds
    return Clock.model_validate({
        **case.clock.model_dump(),
        "percent_elapsed": max(0, elapsed / (case.clock.sla_hours * 3600) * 100),
    })


def pause_clock(case: UMCase, now: datetime, policy: ClockPolicy) -> Clock:
    if not policy.pause_for_missing_info or case.clock.paused:
        return refresh_clock(case, now)
    return Clock.model_validate({
        **refresh_clock(case, now).model_dump(),
        "paused": True,
        "pause_started_at": now,
    })


def resume_clock(case: UMCase, now: datetime) -> Clock:
    if not case.clock.paused:
        return refresh_clock(case, now)
    assert case.clock.pause_started_at is not None
    paused_seconds = (now - case.clock.pause_started_at).total_seconds()
    if paused_seconds < 0:
        raise InvalidRequest("Information receipt cannot precede the pause.")
    return Clock.model_validate({
        **case.clock.model_dump(),
        "paused": False,
        "pause_started_at": None,
        "due_at": case.clock.due_at + timedelta(seconds=paused_seconds),
        "total_paused_seconds": case.clock.total_paused_seconds + paused_seconds,
    })

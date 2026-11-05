from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from statistics import mean, median
from typing import Any
from uuid import UUID

from um_agentic.contracts import Contract, Event, Service
from um_agentic.persistence import Store
from um_agentic.settings import Configuration


@dataclass
class CaseMetric:
    case_id: UUID
    received_at: datetime
    line: str
    urgency: str
    service: Service
    provider_npi: str
    due_at: datetime
    pa_required: bool = True
    eligible: bool = False
    outcome: str | None = None
    decision_at: datetime | None = None
    automatic: bool = False
    pended: bool = False
    override: bool = False
    docs_complete: bool | None = None
    recommendation: str | None = None
    reviewer_starts: dict[str, datetime] = field(default_factory=dict)
    handle_seconds: float = 0

    @property
    def turnaround_hours(self) -> float | None:
        return (
            max(0, (self.decision_at - self.received_at).total_seconds() / 3600)
            if self.decision_at else None
        )


class AnalyticsReport(Contract):
    operational: dict[str, Any]
    codes: tuple[dict[str, Any], ...]
    providers: tuple[dict[str, Any], ...]
    missing_information: tuple[dict[str, Any], ...]
    agent_quality: dict[str, Any]
    advisory_only: bool = True


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _when(raw: Any) -> datetime:
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


class Analytics:
    def __init__(self, store: Store, config: Configuration) -> None:
        self.store = store
        self.config = config

    def _cases(self, events: list[Event]) -> dict[UUID, CaseMetric]:
        cases: dict[UUID, CaseMetric] = {}
        for event in events:
            data = event.payload
            if event.event_type == "case_received":
                cases[event.case_id] = CaseMetric(
                    case_id=event.case_id, received_at=event.occurred_at,
                    line=str(data["line_of_business"]), urgency=str(data["urgency"]),
                    service=Service.model_validate(data["service"]),
                    provider_npi=str(data["provider_npi"]), due_at=_when(data["due_at"]),
                )
            current = cases.get(event.case_id)
            if current is None:
                continue
            if event.event_type == "coverage_checked":
                current.pa_required = data["pa_required"] is True
                current.eligible = data["eligible"] is True and data["benefit_covered"] is True
            elif event.event_type == "information_requested":
                current.pended = True
            elif event.event_type == "criteria_evaluated":
                raw_results = data.get("results")
                if current.docs_complete is None and isinstance(raw_results, list):
                    current.docs_complete = all(
                        isinstance(row, dict) and row.get("status") != "UNKNOWN" for row in raw_results
                    )
            elif event.event_type in {"agent_completed", "agent_failed"} and data.get("agent") == "recommendation":
                output = data.get("outputs")
                if isinstance(output, dict):
                    current.recommendation = str(output["outcome"])
                    current.pended |= output["outcome"] == "PEND_MISSING_INFO"
            elif event.event_type == "review_started":
                current.reviewer_starts[event.actor.actor_id] = event.occurred_at
            elif event.event_type == "human_determination":
                current.outcome = str(data["outcome"])
                current.decision_at = event.occurred_at
                current.override = data["override_of_recommendation"] is True
                started = current.reviewer_starts.get(event.actor.actor_id)
                if started:
                    current.handle_seconds += max(0, (event.occurred_at - started).total_seconds())
            elif event.event_type == "state_transition":
                clock = data.get("clock")
                if isinstance(clock, dict):
                    current.due_at = _when(clock["due_at"])
                if data.get("to") == "AUTO_APPROVED":
                    current.outcome, current.decision_at, current.automatic = "APPROVED", event.occurred_at, True
        return cases

    def report(self, start: date | None = None, end: date | None = None) -> AnalyticsReport:
        events = self.store.events()
        all_cases = self._cases(events)
        lower = datetime.combine(start, time.min, tzinfo=UTC) if start else None
        upper = datetime.combine(end, time.max, tzinfo=UTC) if end else None
        cohort = [
            item for item in all_cases.values()
            if (lower is None or item.received_at >= lower) and (upper is None or item.received_at <= upper)
        ]
        identities = {item.case_id for item in cohort}
        clinical = [item for item in cohort if item.eligible and item.pa_required]
        decided = [item for item in clinical if item.decision_at]
        within = sum(item.decision_at <= item.due_at for item in decided if item.decision_at)
        grouped_times: dict[str, list[float]] = defaultdict(list)
        for item in decided:
            if item.turnaround_hours is not None:
                grouped_times[f"{item.line}:{item.urgency}"].append(item.turnaround_hours)
        human = [item for item in decided if not item.automatic]
        operational = {
            "received": len(cohort),
            "authorization_required": len(clinical),
            "decided": len(decided),
            "percent_within_sla": rate(within, len(decided)),
            "pend_for_info_rate": rate(sum(item.pended for item in clinical), len(clinical)),
            "touchless_clinical_decision_rate": rate(sum(item.automatic for item in decided), len(decided)),
            "override_rate": rate(sum(item.override for item in human), len(human)),
            "mean_reviewer_handle_seconds": mean(item.handle_seconds for item in human) if human else None,
            "overturn_on_appeal": None,
            "appeals_status": "Phase 2 integration required. No appeal rate is inferred.",
            "turnaround_hours": {
                key: {"count": len(values), "mean": mean(values), "median": median(values)}
                for key, values in grouped_times.items()
            },
        }
        by_code: dict[tuple[str, str, str], list[CaseMetric]] = defaultdict(list)
        by_provider: dict[str, list[CaseMetric]] = defaultdict(list)
        for item in clinical:
            by_code[(item.line, item.service.code_system, item.service.code)].append(item)
            by_provider[item.provider_npi].append(item)
        codes: list[dict[str, Any]] = []
        for (line, system, code), items in sorted(by_code.items()):
            completed = [item for item in items if item.decision_at]
            approved = sum(item.outcome == "APPROVED" for item in completed)
            approval_rate = rate(approved, len(completed))
            times = [item.turnaround_hours for item in completed if item.turnaround_hours is not None]
            candidate = (
                len(completed) >= self.config.meaningful_volume
                and approval_rate is not None and approval_rate > self.config.approval_removal_threshold
            )
            codes.append({
                "line_of_business": line, "code_system": system, "code": code,
                "volume": len(items), "decided_volume": len(completed),
                "approval_rate": approval_rate,
                "pend_rate": rate(sum(item.pended for item in items), len(items)),
                "mean_turnaround_hours": mean(times) if times else None,
                "administrative_cost_estimate": sum(not item.automatic for item in items) * self.config.review_cost_estimate,
                "candidate_to_remove_pa": candidate, "advisory_only": True,
            })
        providers = tuple({
            "provider_npi": npi, "volume": len(items),
            "approval_rate": rate(
                sum(item.outcome == "APPROVED" for item in items),
                sum(item.decision_at is not None for item in items),
            ),
            "documentation_completeness_rate": rate(
                sum(item.docs_complete is True for item in items),
                sum(item.docs_complete is not None for item in items),
            ),
            "advisory_only": True,
        } for npi, items in sorted(by_provider.items()))
        missing: Counter[str] = Counter()
        accepted = rejected = total_runs = failed_runs = 0
        for event in events:
            if event.case_id not in identities:
                continue
            if event.event_type == "criteria_evaluated":
                raw = event.payload.get("results")
                if isinstance(raw, list):
                    for row in raw:
                        if isinstance(row, dict) and row.get("status") == "UNKNOWN":
                            missing[str(row["criterion_id"])] += 1
            elif event.event_type == "evidence_verified":
                accepted += int(str(event.payload["accepted"]))
                rejected += int(str(event.payload["rejected"]))
            elif event.event_type in {"agent_completed", "agent_failed"}:
                total_runs += 1
                failed_runs += event.event_type == "agent_failed"
        agreement = sum(
            (item.recommendation == "APPROVE_RECOMMENDED") == (item.outcome == "APPROVED")
            for item in decided if item.recommendation
        )
        return AnalyticsReport(
            operational=operational, codes=tuple(codes), providers=providers,
            missing_information=tuple({"criterion_id": key, "count": value} for key, value in missing.most_common()),
            agent_quality={
                "verifier_rejection_rate": rate(rejected, accepted + rejected),
                "citation_failure_rate": rate(rejected, accepted + rejected),
                "failed_agent_run_rate": rate(failed_runs, total_runs),
                "recommendation_final_agreement": rate(agreement, sum(bool(item.recommendation) for item in decided)),
            },
        )

    def public_reporting(
        self, start: date | None = None, end: date | None = None,
    ) -> dict[str, Any]:
        cases = self._cases(self.store.events())
        clinical = [
            item for item in cases.values()
            if item.line == "MEDICAL" and item.eligible and item.pa_required
            and (start is None or item.received_at.date() >= start)
            and (end is None or item.received_at.date() <= end)
        ]
        result: dict[str, Any] = {
            "schema": "cms0057f_reference_2026.1", "line_scope": "MEDICAL",
            "period_start": str(start) if start else None,
            "period_end": str(end) if end else None,
            "reference_only": True,
            "compliance_review_required": True,
            "approved_after_appeal_percentage": None,
            "appeals_status": "Not implemented in v1.",
            "urgencies": {},
        }
        for urgency in ("STANDARD", "EXPEDITED"):
            cohort = [item for item in clinical if item.urgency == urgency]
            decided = [item for item in cohort if item.decision_at]
            times = [item.turnaround_hours for item in decided if item.turnaround_hours is not None]
            result["urgencies"][urgency] = {
                "requests": len(cohort), "decided": len(decided),
                "approved_percentage": rate(sum(item.outcome == "APPROVED" for item in decided), len(decided)),
                "denied_percentage": rate(sum(item.outcome == "DENIED" for item in decided), len(decided)),
                "partially_approved_percentage": rate(sum(item.outcome == "PARTIALLY_APPROVED" for item in decided), len(decided)),
                "mean_decision_hours": mean(times) if times else None,
                "median_decision_hours": median(times) if times else None,
            }
        return result

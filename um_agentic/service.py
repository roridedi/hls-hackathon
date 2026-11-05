import asyncio
import hashlib
import time
from uuid import UUID

from pydantic import JsonValue

from um_agentic.adapters.eligibility import EligibilityService, MockEligibility
from um_agentic.adapters.intake import IntakeEnvelope, normalize
from um_agentic.adapters.sources import minimum_sources
from um_agentic.agents.base import AgentResult, AgentRunner
from um_agentic.agents.clinical_evidence.runner import SPEC as EXTRACTION
from um_agentic.agents.criteria.runner import SPEC as CRITERIA
from um_agentic.agents.documentation.runner import SPEC as DOCUMENTATION
from um_agentic.agents.documentation.runner import questions_for
from um_agentic.agents.fakes import fake_handlers
from um_agentic.agents.recommendation.runner import recommend
from um_agentic.agents.verifier.runner import SPEC as VERIFIER
from um_agentic.agents.verifier.runner import independently_verify
from um_agentic.contracts import (
    SYSTEM,
    Actor,
    AgentOutcome,
    AgentRun,
    CaseAggregate,
    Confidence,
    ConfidenceComponents,
    Contract,
    DocumentRef,
    Recommendation,
    SourceType,
    Status,
    SummaryOutput,
    UMCase,
)
from um_agentic.errors import InvalidRequest
from um_agentic.llm import FakeLLM, LLMClient
from um_agentic.llm.prompts import load_prompt
from um_agentic.persistence import Store
from um_agentic.rules import CriteriaRegistry, evaluate
from um_agentic.security.injection import detect_injection
from um_agentic.security.roles import INTAKE_ROLES, require_roles
from um_agentic.serialization import canonical_json, content_hash
from um_agentic.settings import Settings
from um_agentic.workflow import Workflow
from um_agentic.workflow.clocks import start_clock


class UMService:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        client: LLMClient | None = None,
        eligibility: EligibilityService | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.config = settings.configuration()
        self.workflow = Workflow(store, self.config)
        self.client = client or FakeLLM(handlers=fake_handlers())
        self.runner = AgentRunner(self.client, self.config)
        self.eligibility = eligibility or MockEligibility()
        self.criteria = CriteriaRegistry()

    def intake(
        self, envelope: IntakeEnvelope, actor: Actor, idempotency_key: str | None = None,
        case_id: UUID | None = None,
    ) -> CaseAggregate:
        require_roles(actor, INTAKE_ROLES)
        request_hash = content_hash(envelope.model_dump(mode="json"))
        if idempotency_key:
            existing = self.store.find_idempotent(idempotency_key, request_hash)
            if existing is not None:
                return self.store.get(existing)
        normalized = normalize(envelope)
        request = normalized.request
        attachments = tuple(
            source.document_ref or DocumentRef(
                document_id=source.source_id, media_type="text/plain",
                sha256=hashlib.sha256("\f".join(source.pages).encode()).hexdigest(),
                page_count=len(source.pages),
            )
            for source in normalized.sources if source.source_type == SourceType.DOCUMENT
        )
        values = {
            **request.model_dump(),
            "channel": envelope.channel,
            "line_of_business": envelope.line_of_business,
            "received_at": envelope.received_at,
            "jurisdiction": envelope.jurisdiction,
            "attachments": attachments,
            "clock": start_clock(
                envelope.received_at, request.urgency,
                self.config.clock_policy(envelope.line_of_business, envelope.jurisdiction),
            ),
        }
        if case_id is not None:
            values["case_id"] = case_id
        case = UMCase.model_validate(values)
        aggregate = CaseAggregate(
            case=case, sources=normalized.sources,
            original_request=envelope.model_dump(mode="json"),
        )
        self.workflow.receive(aggregate, actor, idempotency_key, request_hash)
        self.workflow.transition(case.case_id, Status.NORMALIZED, SYSTEM, "Channel payload normalized.")
        self.workflow.record(
            case.case_id, SYSTEM, "normalization_completed", "Request normalized.",
            normalized.model_dump(mode="json"),
        )
        self.record_deterministic(
            case.case_id, "intake_normalizer",
            {"channel": envelope.channel.value, "payload": envelope.payload},
            normalized.request,
        )
        self.workflow.transition(case.case_id, Status.ADMIN_CHECK, SYSTEM, "Coverage check started.")
        result = self.check_coverage(case.case_id)
        if result.case.status == Status.DOCS_CHECK:
            return asyncio.run(self.analyze_evidence(case.case_id))
        return result

    def check_coverage(self, case_id: UUID) -> CaseAggregate:
        aggregate = self.store.get(case_id)
        coverage = self.eligibility.check(aggregate.case)
        self.workflow.record(
            case_id, SYSTEM, "coverage_checked", coverage.reason,
            coverage.model_dump(mode="json"), {"coverage": coverage},
        )
        self.record_deterministic(
            case_id, "coverage_check",
            {
                "member_token": aggregate.case.member_token,
                "date_of_service": aggregate.case.date_of_service.isoformat(),
                "line_of_business": aggregate.case.line_of_business.value,
                "service": aggregate.case.service.model_dump(mode="json"),
            },
            coverage,
        )
        if not coverage.eligible or not coverage.benefit_covered:
            return self.workflow.transition(
                case_id, Status.ADMIN_PEND, SYSTEM, coverage.reason,
            )
        if not coverage.pa_required:
            self.workflow.transition(case_id, Status.NO_PA_REQUIRED, SYSTEM, coverage.reason)
            return self.workflow.transition(
                case_id, Status.CLOSED, SYSTEM, "No prior authorization is required.",
            )
        return self.workflow.transition(
            case_id, Status.DOCS_CHECK, SYSTEM, "Clinical documentation check started.",
        )

    def record_run[OutputT: Contract](self, result: AgentResult[OutputT]) -> None:
        if result.run.case_id is None:
            raise InvalidRequest("Case agent runs require a case identifier.")
        self.workflow.record(
            result.run.case_id, SYSTEM,
            "agent_failed" if result.run.error else "agent_completed",
            result.run.error or "Agent proposal recorded.",
            result.run.model_dump(mode="json"),
            run=result.run,
        )

    def fail_safe(self, case_id: UUID, reason: str) -> CaseAggregate:
        current = self.store.get(case_id)
        recommendation = Recommendation(
            outcome=AgentOutcome.PEND_CLINICAL_REVIEW, confidence=Confidence.LOW,
            components=ConfidenceComponents(
                all_required_met=False,
                verified_evidence_count=sum(item.verified for item in current.evidence),
                contradiction_count=len(current.contradictions), unstructured_source_count=0,
                min_extraction_score=0, agent_failed=True, explanation=reason,
            ),
            reasons=(reason,), routing_queue="nurse",
        )
        self.workflow.record(
            case_id, SYSTEM, "human_review_required", reason, {"failure": reason},
            {"failures": (*current.failures, reason), "recommendation": recommendation},
        )
        return self.workflow.transition(
            case_id, Status.IN_CLINICAL_REVIEW, SYSTEM,
            "A clinical reviewer must review the incomplete analysis.",
        )

    async def analyze_evidence(self, case_id: UUID) -> CaseAggregate:
        current = self.store.get(case_id)
        try:
            criteria = current.criteria_snapshot or self.criteria.for_case(current.case)
        except InvalidRequest as exc:
            return self.fail_safe(case_id, str(exc))
        self.workflow.record(
            case_id, SYSTEM, "criteria_selected", "Versioned synthetic criteria selected.",
            criteria.model_dump(mode="json"), {"criteria_snapshot": criteria},
        )
        flagged = tuple(
            source.source_id for source in current.sources
            if detect_injection("\n".join(source.pages) if source.pages else canonical_json(source.resource))
        )
        if flagged:
            self.workflow.record(
                case_id, SYSTEM, "injection_detected",
                "Instruction like source text was retained as untrusted content.",
                {"source_ids": list(flagged)},
                {"injection_sources": flagged},
            )
        sources = minimum_sources(current.sources)
        self.workflow.transition(
            case_id, Status.CLINICAL_ANALYSIS, SYSTEM, "Cited evidence extraction started.",
        )
        source_inputs: list[JsonValue] = [s.model_dump(mode="json") for s in sources]
        extracted, documented = await asyncio.gather(
            self.runner.run(
                EXTRACTION, case_id, {"sources": source_inputs}, criteria.version,
            ),
            self.runner.run(
                DOCUMENTATION, case_id, {
                    "sources": source_inputs, "criteria": criteria.model_dump(mode="json"),
                    "as_of": current.case.received_at.date().isoformat(),
                }, criteria.version,
            ),
        )
        self.record_run(extracted)
        self.record_run(documented)
        if extracted.output is None:
            return self.fail_safe(case_id, extracted.run.error or "Evidence extraction failed.")
        if documented.output is None:
            return self.fail_safe(case_id, documented.run.error or "Documentation analysis failed.")
        evidence = tuple(
            item.model_copy(update={"verified": False, "verifier_note": None})
            for item in extracted.output.evidence
        )
        if len({item.evidence_id for item in evidence}) != len(evidence):
            return self.fail_safe(case_id, "The extractor returned duplicate evidence identifiers.")
        self.workflow.record(
            case_id, SYSTEM, "evidence_extracted", "Unverified evidence proposals recorded.",
            {"count": len(evidence)}, {"evidence": evidence},
        )
        self.workflow.transition(
            case_id, Status.VERIFICATION, SYSTEM, "Independent citation verification started.",
        )
        verified = await self.runner.run(
            VERIFIER, case_id,
            {
                "sources": [s.model_dump(mode="json") for s in sources],
                "evidence": [item.model_dump(mode="json") for item in evidence],
            },
            criteria.version,
        )
        self.record_run(verified)
        if verified.output is None:
            return self.fail_safe(case_id, verified.run.error or "Evidence verification failed.")
        evidence, contradictions = independently_verify(evidence, current.sources, verified.output)
        current = self.workflow.record(
            case_id, SYSTEM, "evidence_verified", "Citations and cross source consistency checked.",
            {
                "accepted": sum(item.verified for item in evidence),
                "rejected": sum(not item.verified for item in evidence),
                "contradictions": len(contradictions),
            },
            {"evidence": evidence, "contradictions": contradictions},
        )
        if any(c.requires_clinical_judgment for c in criteria.criteria):
            mapped = await self.runner.run(
                CRITERIA, case_id,
                {
                    "criteria": criteria.model_dump(mode="json"),
                    "evidence": [item.model_dump(mode="json") for item in evidence if item.verified],
                },
                criteria.version,
            )
            self.record_run(mapped)
            if mapped.output is None:
                return self.fail_safe(case_id, mapped.run.error or "Clinical mapping failed.")
            allowed = {c.id for c in criteria.criteria if c.requires_clinical_judgment}
            valid_ids = {item.evidence_id for item in evidence if item.verified}
            if any(
                r.criterion_id not in allowed or not set(r.evidence_ids) <= valid_ids
                for r in mapped.output.results
            ):
                return self.fail_safe(case_id, "Clinical mapping contained unsupported references.")
        evaluation = evaluate(
            criteria, evidence, current.case.received_at.date(), contradictions,
        )
        documentation = questions_for(criteria, evaluation)
        if any(
            question.criterion_id not in {c.id for c in criteria.criteria}
            for question in documented.output.questions
        ):
            return self.fail_safe(case_id, "Documentation contained an unknown criterion reference.")
        recommendation = recommend(
            criteria, evaluation, evidence, contradictions, documentation, self.config,
        )
        summary = SummaryOutput(
            summary=" ".join(recommendation.reasons),
            evidence_ids=tuple(sorted({
                identity for result in evaluation.results for identity in result.evidence_ids
            })),
        )
        self.workflow.record(
            case_id, SYSTEM, "criteria_evaluated", "Deterministic criteria evaluated using verified evidence.",
            evaluation.model_dump(mode="json"),
            {
                "evaluation": evaluation, "documentation": documentation,
                "recommendation": recommendation, "reviewer_summary": summary,
            },
        )
        self.record_deterministic(
            case_id, "recommendation",
            {
                "evaluation": evaluation.model_dump(mode="json"),
                "evidence": [item.model_dump(mode="json") for item in evidence],
                "criteria": criteria.model_dump(mode="json"),
                "contradictions": [c.model_dump(mode="json") for c in contradictions],
                "documentation": documentation.model_dump(mode="json"),
                "extraction_score_threshold": self.config.extraction_score_threshold,
            },
            recommendation, criteria.version,
        )
        self.workflow.transition(
            case_id, Status.RECOMMENDED, SYSTEM, "Validated recommendation assembled.",
        )
        if (
            self.config.auto_approve_enabled[current.case.line_of_business]
            and criteria.auto_approve_eligible
            and evaluation.overall == "ALL_MET"
            and not contradictions
            and not any(c.requires_clinical_judgment for c in criteria.criteria)
            and all(result.status.value == "MET" for result in evaluation.results)
        ):
            self.workflow.transition(
                case_id, Status.AUTO_APPROVED, SYSTEM,
                "Every deterministic criterion has verified evidence and the feature is enabled.",
                {"automatic_approval": True},
            )
            self.workflow.transition(
                case_id, Status.NOTIFICATION_PENDING_APPROVAL, SYSTEM,
                "Automatic approval letters still require human release.",
            )
            from um_agentic.review import ReviewService

            return await ReviewService(self).draft_letters(case_id)
        return self.workflow.transition(
            case_id,
            Status.IN_PHYSICIAN_REVIEW if recommendation.routing_queue == "physician"
            else Status.IN_CLINICAL_REVIEW,
            SYSTEM, "Routed to an authenticated human reviewer.",
        )

    def record_deterministic(
        self, case_id: UUID, agent: str, inputs: dict[str, JsonValue], output: Contract,
        criteria_version: str | None = None,
    ) -> None:
        version, prompt, prompt_hash = load_prompt(agent)
        started = time.monotonic()
        serialized = output.model_dump(mode="json")
        run = AgentRun(
            case_id=case_id, agent=agent, model_id="deterministic-rules-v1",
            prompt_version=version, prompt_hash=prompt_hash, criteria_version=criteria_version,
            prompt_text=prompt,
            inputs_hash=content_hash(inputs), inputs=inputs, outputs=serialized,
            outputs_hash=content_hash(serialized), latency_ms=(time.monotonic() - started) * 1000,
            input_tokens=0, output_tokens=0, attempts=1,
        )
        self.record_run(AgentResult(output=output, run=run))

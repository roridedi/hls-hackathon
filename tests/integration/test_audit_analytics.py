import asyncio
import io
import json

from pypdf import PdfReader

from um_agentic.adapters.blob import LocalBlobStore
from um_agentic.adapters.intake import IntakeEnvelope
from um_agentic.analytics import Analytics
from um_agentic.analytics.insights import weekly_insights
from um_agentic.api.models import LetterRelease
from um_agentic.audit import AuditBuilder
from um_agentic.audit.replay import replay_case
from um_agentic.contracts import Actor
from um_agentic.persistence import Store
from um_agentic.review import ReviewService
from um_agentic.service import UMService
from um_agentic.settings import Settings
from um_agentic.synthetic import golden_fixture


def test_closed_case_audit_json_pdf_and_replay(
    settings: Settings, store: Store, physician: Actor,
) -> None:
    service = UMService(store, settings)
    review = ReviewService(service)
    case = service.intake(IntakeEnvelope.model_validate(golden_fixture("G1")["request"]), physician)
    review.accept(case.case.case_id, physician)
    review.release(case.case.case_id, physician, LetterRelease(confirm=True))
    blobs = LocalBlobStore(settings.storage_path)
    builder = AuditBuilder(store, blobs)
    packet = builder.build(case.case.case_id)
    assert packet.case.original_request["payload"]
    assert packet.case.determination is not None
    assert packet.agent_runs and packet.events
    encoded = blobs.get(f"audit/{case.case.case_id}.json")
    assert json.loads(encoded)["packet_hash"] == packet.packet_hash
    pdf = blobs.get(f"audit/{case.case.case_id}.pdf")
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
    for required in ("Request as received", "Evidence and verification", "Human determination", "Letters as released"):
        assert required in text
    assert builder.build(case.case.case_id).packet_hash == packet.packet_hash
    assert blobs.get(f"audit/{case.case.case_id}.pdf") == pdf
    replay = replay_case(store, case.case.case_id, service.config)
    assert replay.outputs_match
    assert replay.runs_reproduced == len(packet.agent_runs)


def test_analytics_are_event_derived_and_insights_are_advisory(
    settings: Settings, store: Store, physician: Actor,
) -> None:
    enabled = settings.model_copy(update={"auto_approve_pharmacy": True})
    service = UMService(store, enabled)
    service.intake(IntakeEnvelope.model_validate(golden_fixture("G1")["request"]), physician)
    service.intake(IntakeEnvelope.model_validate(golden_fixture("G2")["request"]), physician)
    analytics = Analytics(store, service.config)
    report = analytics.report()
    assert report.operational["received"] == 2
    assert report.operational["touchless_clinical_decision_rate"] == 1
    assert report.operational["pend_for_info_rate"] == 0.5
    assert report.missing_information[0]["criterion_id"] == "lifestyle_program"
    assert report.agent_quality["citation_failure_rate"] == 0
    assert not any(row["candidate_to_remove_pa"] for row in report.codes)
    public = analytics.public_reporting()
    assert public["line_scope"] == "MEDICAL"
    assert public["approved_after_appeal_percentage"] is None
    insight = asyncio.run(weekly_insights(service))
    assert insight.summary.advisory_only

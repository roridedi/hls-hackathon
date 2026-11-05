import json
from uuid import UUID

from um_agentic.adapters.blob import BlobStore
from um_agentic.audit.pdf import build_pdf
from um_agentic.contracts import AgentRun, CaseAggregate, Contract, Event, Status
from um_agentic.errors import Conflict
from um_agentic.persistence import Store
from um_agentic.serialization import content_hash


class AuditPacket(Contract):
    schema_version: str = "2026.1"
    case: CaseAggregate
    events: tuple[Event, ...]
    agent_runs: tuple[AgentRun, ...]
    packet_hash: str


class AuditBuilder:
    def __init__(self, store: Store, blobs: BlobStore) -> None:
        self.store = store
        self.blobs = blobs

    def build(self, case_id: UUID) -> AuditPacket:
        case = self.store.get(case_id)
        if case.case.status != Status.CLOSED:
            raise Conflict("An audit packet can be finalized only for a closed case.")
        if not self.store.verify_chain(case_id):
            raise Conflict("Audit integrity verification failed. Export is blocked.")
        events = tuple(self.store.events(case_id))
        runs = tuple(self.store.runs(case_id))
        if {run.run_id for run in runs} != set(case.agent_run_ids):
            raise Conflict("The case agent run inventory has changed.")
        recorded = {
            str(event.payload.get("run_id")): event.payload
            for event in events if event.event_type in {"agent_completed", "agent_failed"}
        }
        for run in runs:
            serialized = run.model_dump(mode="json")
            event_payload = recorded.get(str(run.run_id))
            if (
                run.inputs_hash != content_hash(run.inputs)
                or run.outputs_hash != (content_hash(run.outputs) if run.outputs is not None else None)
                or run.prompt_hash != content_hash(run.prompt_text)
                or event_payload is None
                or any(event_payload.get(key) != value for key, value in serialized.items())
            ):
                raise Conflict("Agent run integrity verification failed.")
        body = {
            "schema_version": "2026.1",
            "case": case.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in events],
            "agent_runs": [run.model_dump(mode="json") for run in runs],
        }
        packet = AuditPacket.model_validate({**body, "packet_hash": content_hash(body)})
        encoded = packet.model_dump_json(indent=2).encode()
        self.blobs.put(f"audit/{case_id}.json", encoded, "application/json")
        sections = [
            ("Request as received", json.dumps(case.original_request, indent=2)),
            ("Normalization result", case.case.model_dump_json(indent=2)),
            ("Coverage check", case.coverage.model_dump_json(indent=2) if case.coverage else "Not performed."),
            ("Evidence and verification", json.dumps([e.model_dump(mode="json") for e in case.evidence], indent=2)),
            ("Criteria and evaluation", json.dumps({
                "criteria": case.criteria_snapshot.model_dump(mode="json") if case.criteria_snapshot else None,
                "evaluation": case.evaluation.model_dump(mode="json") if case.evaluation else None,
            }, indent=2)),
            ("Recommendation and confidence", case.recommendation.model_dump_json(indent=2) if case.recommendation else "Not applicable."),
            ("Human determination", case.determination.model_dump_json(indent=2) if case.determination else "No human clinical determination."),
            ("Letters as released", "\n\n".join(letter.text for letter in case.letters)),
            ("Full clock timeline and human actions", json.dumps([
                event.model_dump(mode="json") for event in events
            ], indent=2)),
            ("Model and prompt provenance", json.dumps([run.model_dump(mode="json") for run in runs], indent=2)),
            ("Packet integrity", packet.packet_hash),
        ]
        self.blobs.put(
            f"audit/{case_id}.pdf", build_pdf("Utilization management audit packet", sections),
            "application/pdf",
        )
        return packet

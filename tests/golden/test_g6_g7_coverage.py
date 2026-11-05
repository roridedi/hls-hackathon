import json
from pathlib import Path

import pytest

from um_agentic.adapters.intake import IntakeEnvelope
from um_agentic.contracts import Actor, Status
from um_agentic.llm import FakeLLM
from um_agentic.persistence import Store
from um_agentic.service import UMService
from um_agentic.settings import Settings


@pytest.mark.parametrize(("scenario", "expected"), [("g6", Status.CLOSED), ("g7", Status.ADMIN_PEND)])
def test_coverage_short_circuits_clinical_agents(
    scenario: str, expected: Status, settings: Settings, store: Store, intake_actor: Actor
) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / f"{scenario}.json").read_text())
    fake = FakeLLM()
    service = UMService(store, settings, client=fake)
    result = service.intake(IntakeEnvelope.model_validate(fixture["request"]), intake_actor)
    assert result.case.status == expected
    assert fake.calls == []
    assert {run.agent for run in store.runs(result.case.case_id)} == {
        "intake_normalizer", "coverage_check",
    }
    if scenario == "g6":
        assert any(
            e.payload.get("to") == "NO_PA_REQUIRED" for e in store.events(result.case.case_id)
        )
    else:
        assert result.coverage is not None and not result.coverage.eligible


def test_intake_is_idempotent(settings: Settings, store: Store, intake_actor: Actor) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "g6.json").read_text())
    service = UMService(store, settings)
    request = IntakeEnvelope.model_validate(fixture["request"])
    first = service.intake(request, intake_actor, "golden-g6")
    repeated = service.intake(request, intake_actor, "golden-g6")
    assert first == repeated
    assert len(store.all_cases()) == 1

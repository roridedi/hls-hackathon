import json
from pathlib import Path

import pytest

from um_agentic.adapters.intake import IntakeEnvelope
from um_agentic.adapters.sources import supports
from um_agentic.contracts import Actor
from um_agentic.persistence import Store
from um_agentic.service import UMService
from um_agentic.settings import Settings


@pytest.mark.parametrize("scenario", ["g1", "g2", "g3", "g4", "g5", "g8"])
def test_all_golden_evidence_has_supported_citations(
    scenario: str, settings: Settings, store: Store, intake_actor: Actor,
) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / f"{scenario}.json").read_text())
    service = UMService(store, settings)
    result = service.intake(IntakeEnvelope.model_validate(fixture["request"]), intake_actor)
    assert result.evidence
    assert all(item.verified and supports(item, result.sources)[0] for item in result.evidence)
    if scenario == "g3":
        assert len(result.contradictions) == 1
        assert result.contradictions[0].fact_type == "therapy_duration"
    elif scenario == "g8":
        assert result.injection_sources == ("g8_note",)
        assert any(e.event_type == "injection_detected" for e in store.events(result.case.case_id))

import json
from pathlib import Path

import pytest

from um_agentic.adapters.intake import IntakeEnvelope
from um_agentic.contracts import Actor, AgentOutcome, Status
from um_agentic.persistence import Store
from um_agentic.service import UMService
from um_agentic.settings import Settings


@pytest.mark.parametrize("number", [1, 2, 3, 4, 5, 8])
def test_golden_recommendation(
    number: int, settings: Settings, store: Store, intake_actor: Actor,
) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / f"g{number}.json").read_text())
    service = UMService(store, settings)
    result = service.intake(IntakeEnvelope.model_validate(fixture["request"]), intake_actor)
    assert result.recommendation is not None
    assert result.recommendation.outcome.value == fixture["expected"]
    assert not result.automatic_approval
    assert result.determination is None
    if number == 2:
        assert result.documentation is not None
        assert len(result.documentation.questions) == 1
        assert "lifestyle program" in result.documentation.questions[0].text
    if number == 4:
        assert result.evaluation is not None
        assert result.evaluation.results[0].status.value == "NOT_MET"
        assert result.evaluation.results[0].evidence_ids
    assert store.verify_chain(result.case.case_id)


def test_g1_auto_approval_requires_opt_in(
    settings: Settings, store: Store, intake_actor: Actor,
) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "g1.json").read_text())
    enabled = settings.model_copy(update={"auto_approve_pharmacy": True})
    result = UMService(store, enabled).intake(
        IntakeEnvelope.model_validate(fixture["request"]), intake_actor,
    )
    assert result.case.status == Status.NOTIFICATION_PENDING_APPROVAL
    assert all(letter.status == "DRAFT" for letter in result.letters)
    assert result.automatic_approval
    assert result.recommendation is not None
    assert result.recommendation.outcome == AgentOutcome.APPROVE_RECOMMENDED

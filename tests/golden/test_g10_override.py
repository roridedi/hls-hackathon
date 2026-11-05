import pytest

from um_agentic.adapters.intake import IntakeEnvelope
from um_agentic.api.models import DeterminationRequest, LetterRelease
from um_agentic.contracts import Actor, ActorKind, DeterminationOutcome, Role, Status
from um_agentic.errors import Forbidden, InvalidRequest
from um_agentic.persistence import Store
from um_agentic.review import ReviewService
from um_agentic.service import UMService
from um_agentic.settings import Settings
from um_agentic.synthetic import golden_fixture


def test_g10_only_physician_can_override_approval_to_denial(
    settings: Settings, store: Store, intake_actor: Actor, physician: Actor,
) -> None:
    service = UMService(store, settings)
    review = ReviewService(service)
    case = service.intake(IntakeEnvelope.model_validate(golden_fixture("G10")["request"]), intake_actor)
    denial = DeterminationRequest(
        outcome=DeterminationOutcome.DENIED,
        specific_reason_code="SYNTHETIC_CLINICAL_REVIEW",
        plain_language_reason="The requested dose is not supported by the records we received.",
        override_rationale="The physician identified a dose concern requiring a different treatment plan.",
    )
    for role in (Role.NURSE, Role.ADMIN, Role.SUPERVISOR, Role.PHARMACIST):
        actor = Actor(actor_id=f"synthetic-{role}", kind=ActorKind.HUMAN, roles={role})
        with pytest.raises(Forbidden):
            review.determine(case.case.case_id, actor, denial)
    with pytest.raises(InvalidRequest):
        review.determine(
            case.case.case_id, physician, denial.model_copy(update={"specific_reason_code": None}),
        )
    result = review.determine(case.case.case_id, physician, denial)
    assert result.case.status == Status.NOTIFICATION_PENDING_APPROVAL
    assert result.determination is not None and result.determination.override_of_recommendation
    assert all(letter.status == "DRAFT" for letter in result.letters)
    for letter in result.letters:
        assert denial.plain_language_reason in letter.text
        assert denial.override_rationale in letter.text
        assert "appeal" in letter.text
    assert any(event.event_type == "human_determination" for event in store.events(case.case.case_id))
    result = review.release(case.case.case_id, physician, LetterRelease(confirm=True))
    assert result.case.status == Status.CLOSED
    assert all(letter.approved_by == physician.actor_id for letter in result.letters)
    assert store.verify_chain(case.case.case_id)

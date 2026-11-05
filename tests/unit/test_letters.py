import pytest

from um_agentic.adapters.intake import IntakeEnvelope
from um_agentic.api.models import LetterRelease
from um_agentic.contracts import Actor, ActorKind, Role
from um_agentic.errors import Forbidden, InvalidRequest
from um_agentic.letters.templates import reading_grade
from um_agentic.persistence import Store
from um_agentic.review import ReviewService
from um_agentic.service import UMService
from um_agentic.settings import Settings
from um_agentic.synthetic import golden_fixture


def test_member_readability_and_release_guards(
    settings: Settings, store: Store, physician: Actor,
) -> None:
    service = UMService(store, settings)
    review = ReviewService(service)
    case = service.intake(IntakeEnvelope.model_validate(golden_fixture("G1")["request"]), physician)
    pending = review.accept(case.case.case_id, physician)
    member = next(letter for letter in pending.letters if letter.recipient == "member")
    assert 6 <= reading_grade(member.text) <= 8
    with pytest.raises(InvalidRequest):
        review.release(case.case.case_id, physician, LetterRelease(confirm=True, member_text="Approved."))
    agent = Actor(actor_id="agent", kind=ActorKind.AGENT, roles={Role.PHYSICIAN})
    with pytest.raises(Forbidden):
        review.release(case.case.case_id, agent, LetterRelease(confirm=True))

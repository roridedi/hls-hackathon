import pytest

from um_agentic.adapters.intake import IntakeEnvelope, NormalizedIntake, normalize
from um_agentic.contracts import Channel
from um_agentic.errors import InvalidRequest
from um_agentic.synthetic import channel_payload, golden_fixture


@pytest.mark.parametrize("channel", list(Channel))
def test_normalization_round_trip_all_channels(channel: Channel) -> None:
    envelope = IntakeEnvelope.model_validate(golden_fixture("G1")["request"])
    baseline = normalize(envelope)
    converted = envelope.model_copy(update={
        "channel": channel, "payload": channel_payload(channel, baseline.request),
    })
    normalized = normalize(converted)
    assert normalized.request == baseline.request
    assert normalized.round_trip() == converted.payload
    assert NormalizedIntake.model_validate_json(normalized.model_dump_json()) == normalized


def test_invalid_fhir_and_ambiguous_request_are_rejected() -> None:
    envelope = IntakeEnvelope.model_validate(golden_fixture("G1")["request"])
    with pytest.raises(InvalidRequest):
        normalize(envelope.model_copy(update={"payload": {"resourceType": "Claim"}}))


def test_unrecognized_urgency_is_not_silently_standard() -> None:
    envelope = IntakeEnvelope.model_validate(golden_fixture("G3")["request"])
    payload = {**envelope.payload, "urgency_code": "unknown"}
    with pytest.raises(InvalidRequest):
        normalize(envelope.model_copy(update={"payload": payload}))

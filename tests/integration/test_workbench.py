from fastapi.testclient import TestClient

from um_agentic.api.app import create_app
from um_agentic.settings import Settings

PHYSICIAN = {"Authorization": "Bearer dev-physician"}
NURSE = {"Authorization": "Bearer dev-nurse"}
AUDITOR = {"Authorization": "Bearer dev-auditor"}


def test_review_api_releases_only_after_human_approval(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        received = client.post("/demo/G1", headers=PHYSICIAN)
        assert received.status_code == 201
        case = received.json()
        identity = case["case"]["case_id"]
        base = f"/cases/{identity}"
        source = client.get(
            f"{base}/evidence/{case['evidence'][0]['evidence_id']}/source", headers=NURSE,
        )
        assert source.status_code == 200 and source.json()["highlight_end"] > source.json()["highlight_start"]
        assert client.post(f"{base}/actions/accept", headers=AUDITOR).status_code == 403
        approved = client.post(f"{base}/actions/accept", headers=NURSE)
        assert approved.status_code == 200
        assert approved.json()["case"]["status"] == "NOTIFICATION_PENDING_APPROVAL"
        assert client.post(f"{base}/actions/release", headers=AUDITOR, json={"confirm": True}).status_code == 403
        assert client.post(f"{base}/actions/release", headers=NURSE, json={"confirm": False}).status_code == 422
        released = client.post(f"{base}/actions/release", headers=NURSE, json={"confirm": True})
        assert released.status_code == 200, released.text
        assert released.json()["case"]["status"] == "CLOSED"


def test_dtr_and_information_resume(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        current = client.post("/demo/G2", headers=NURSE).json()
        base = f"/cases/{current['case']['case_id']}"
        questionnaire = client.get(f"{base}/questionnaire", headers=NURSE).json()
        assert questionnaire["resourceType"] == "Questionnaire"
        assert questionnaire["item"][0]["linkId"] == "lifestyle_program"
        questions = current["documentation"]["questions"]
        pended = client.post(f"{base}/actions/request-info", headers=NURSE, json={"questions": questions})
        assert pended.status_code == 200
        assert pended.json()["case"]["status"] == "PEND_MISSING_INFO"
        assert not pended.json()["case"]["clock"]["paused"]
        resumed = client.post(f"{base}/actions/information", headers=NURSE, json={"sources": [{
            "source_id": "additional_lifestyle", "source_type": "DOCUMENT",
            "pages": ["Lifestyle program: yes; date: 2026-10-01\n"],
        }]})
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["recommendation"]["outcome"] == "APPROVE_RECOMMENDED"


def test_authenticated_claims_are_not_caller_controlled(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        current = client.post("/demo/G1", headers=NURSE).json()
        response = client.post(
            f"/cases/{current['case']['case_id']}/actions/determine",
            headers=NURSE,
            json={
                "outcome": "DENIED", "reviewer_role": "physician_reviewer",
                "specific_reason_code": "X", "plain_language_reason": "Synthetic reason.",
            },
        )
        assert response.status_code == 422

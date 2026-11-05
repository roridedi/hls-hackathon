from fastapi.testclient import TestClient

from um_agentic.api.app import create_app
from um_agentic.settings import Settings


def test_health_and_static_role_map(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/me").status_code == 401
        assert client.get("/me", headers={"Authorization": "Bearer physician"}).status_code == 401
        response = client.get("/me", headers={"Authorization": "Bearer dev-physician"})
        assert response.status_code == 200
        assert response.json()["roles"] == ["physician_reviewer"]

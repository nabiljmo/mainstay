from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_reports_api_ok():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["api"] == "ok"


def test_health_reports_db_state_without_database():
    """With no database configured the API still answers, honestly."""
    response = client.get("/health")
    assert response.json()["db"] in {"not_configured", "ok", "unreachable"}

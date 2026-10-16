from fastapi.testclient import TestClient

from argus_api.main import app


def test_health_needs_no_cloud() -> None:
    assert TestClient(app).get("/health").json() == {"status": "ok"}

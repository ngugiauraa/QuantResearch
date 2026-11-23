import pytest
from fastapi.testclient import TestClient

from quantresearch.api.server import create_app
from quantresearch.api.services import Services
from quantresearch.core.config import Paths

PAGES = "https://quantresearch-dashboard.pages.dev"


def preflight(client, origin):
    return client.options("/api/health", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})


@pytest.fixture()
def services(tmp_path):
    return Services(Paths(tmp_path))


def test_only_the_local_dev_origins_are_allowed_by_default(services, monkeypatch):
    monkeypatch.delenv("QR_CORS_ORIGINS", raising=False)
    client = TestClient(create_app(services))
    assert preflight(client, "http://localhost:3000").headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "access-control-allow-origin" not in preflight(client, PAGES).headers


def test_extra_origins_are_opt_in_and_trailing_slashes_are_ignored(services, monkeypatch):
    monkeypatch.setenv("QR_CORS_ORIGINS", f" {PAGES}/ , https://other.example ,")
    client = TestClient(create_app(services))
    assert preflight(client, PAGES).headers.get("access-control-allow-origin") == PAGES
    assert preflight(client, "https://other.example").headers.get("access-control-allow-origin") == "https://other.example"
    assert "access-control-allow-origin" not in preflight(client, "https://evil.example").headers
    assert preflight(client, "http://127.0.0.1:3000").headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"

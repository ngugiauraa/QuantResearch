"""The headline refresh answers quickly with a plain reason when GDELT refuses, instead of a 502."""
import io
import json
import time
import urllib.error

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from quantresearch.api import server
from quantresearch.api.server import create_app
from quantresearch.api.services import Services
from quantresearch.core.config import Paths
from quantresearch.external import sources as src


@pytest.fixture()
def svc(tmp_path):
    return Services(Paths(tmp_path))


def too_many(*a, **kw):
    raise urllib.error.HTTPError("https://api.gdeltproject.org", 429, "Too Many Requests", {}, io.BytesIO(b""))


def test_a_429_answers_200_with_the_reason_and_keeps_stored_headlines(svc, monkeypatch):
    svc.p.external.mkdir(parents=True, exist_ok=True)
    (svc.p.external / "news.json").write_text(json.dumps({"items": [{"title": "old"}], "fetched_at": "2026-11-26", "source": "GDELT"}), encoding="utf-8")
    monkeypatch.setattr(src, "http_get", too_many)
    body = TestClient(create_app(svc)).post("/api/intel/news/refresh")
    assert body.status_code == 200
    data = body.json()
    assert data["refreshed"] is False and "too many requests" in data["refresh_error"] and data["items"] == [{"title": "old"}]


def test_after_a_429_the_api_does_not_ask_gdelt_again_during_the_cooldown(svc, monkeypatch):
    calls = []
    monkeypatch.setattr(src, "http_get", lambda *a, **kw: (calls.append(1), too_many())[1])
    client = TestClient(create_app(svc))
    client.post("/api/intel/news/refresh")
    second = client.post("/api/intel/news/refresh").json()
    assert len(calls) == 1 and "Try again in" in second["refresh_error"]


def test_a_hanging_source_is_cut_off_at_the_deadline(svc, monkeypatch):
    monkeypatch.setattr(server, "NEWS_DEADLINE_S", 0.3)
    monkeypatch.setattr(src, "http_get", lambda *a, **kw: time.sleep(2) or b"{}")
    started = time.monotonic()
    data = TestClient(create_app(svc)).post("/api/intel/news/refresh").json()
    assert time.monotonic() - started < 1.5 and "did not answer within" in data["refresh_error"]


def test_the_refresh_asks_once_with_a_short_timeout(svc, monkeypatch):
    seen = {}

    def getter(url, timeout=90, retries=3):
        seen.update(timeout=timeout, retries=retries)
        return json.dumps({"articles": [{"seendate": "20261127T120000Z", "title": "Gold rises", "url": "https://x.test/a", "domain": "x.test", "language": "English"}]}).encode()

    monkeypatch.setattr(src, "http_get", getter)
    monkeypatch.setattr(type(svc.knowledge), "ingest_news", lambda self, rows: None)
    data = TestClient(create_app(svc)).post("/api/intel/news/refresh").json()
    assert data["refreshed"] is True and data["refresh_error"] is None and data["items"][0]["title"] == "Gold rises"
    assert seen == {"timeout": server.NEWS_DEADLINE_S, "retries": 1}


def test_http_get_does_not_retry_a_4xx(monkeypatch):
    calls = []

    def opener(*a, **kw):
        calls.append(1)
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b""))

    monkeypatch.setattr(src.urllib.request, "urlopen", opener)
    with pytest.raises(urllib.error.HTTPError):
        src.http_get("https://example.test")
    assert len(calls) == 1

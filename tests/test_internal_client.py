"""
Home/Explore/Admin Integration Phase — app/internal_client.py, the one
place this service talks to app.rithavo.com's internal service API.
httpx is monkeypatched at the module boundary for every test here,
matching the existing convention (test_photo_access.py) for this
service's other server-to-server boundary — never a real network call.
"""

import httpx
import pytest

import config
from app.internal_client import InternalServiceError, get, post, post_file

TEST_SECRET = "test-internal-secret"


@pytest.fixture(autouse=True)
def _secret_and_base_url(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_SERVICE_SECRET", TEST_SECRET)
    monkeypatch.setattr(config, "INTERNAL_SERVICE_BASE_URL", "https://app.rithavo.com")


class _FakeResponse:
    def __init__(self, status_code, json_body=None):
        self.status_code = status_code
        self._json_body = json_body or {}

    def json(self):
        return self._json_body


def test_get_sends_bearer_header_and_returns_json(monkeypatch):
    captured = {}

    def _fake_get(url, params, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeResponse(200, {"ok": True})

    import app.internal_client as client_module
    monkeypatch.setattr(client_module.httpx, "get", _fake_get)

    result = get("/internal/api/explore/stories")
    assert result == {"ok": True}
    assert captured["url"] == "https://app.rithavo.com/internal/api/explore/stories"
    assert captured["headers"]["Authorization"] == f"Bearer {TEST_SECRET}"


def test_get_raises_on_non_2xx(monkeypatch):
    import app.internal_client as client_module
    monkeypatch.setattr(client_module.httpx, "get", lambda url, params, headers, timeout: _FakeResponse(404))
    with pytest.raises(InternalServiceError) as exc_info:
        get("/internal/api/explore/stories/999")
    assert exc_info.value.status_code == 404


def test_get_raises_on_network_error(monkeypatch):
    import app.internal_client as client_module

    def _raise(url, params, headers, timeout):
        raise httpx.ConnectTimeout("simulated timeout")

    monkeypatch.setattr(client_module.httpx, "get", _raise)
    with pytest.raises(InternalServiceError) as exc_info:
        get("/internal/api/explore/stories")
    assert exc_info.value.status_code == 502


def test_get_fails_closed_when_secret_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_SERVICE_SECRET", None)
    with pytest.raises(InternalServiceError) as exc_info:
        get("/internal/api/explore/stories")
    assert exc_info.value.status_code == 503


def test_post_sends_json_body(monkeypatch):
    captured = {}

    def _fake_post(url, json, headers, timeout):
        captured["json"] = json
        return _FakeResponse(200, {"created": True})

    import app.internal_client as client_module
    monkeypatch.setattr(client_module.httpx, "post", _fake_post)

    result = post("/internal/api/explore/stories", {"headline": "H"})
    assert result == {"created": True}
    assert captured["json"] == {"headline": "H"}


def test_post_file_sends_multipart(monkeypatch):
    captured = {}

    def _fake_post(url, files, headers, timeout):
        captured["files"] = files
        return _FakeResponse(200, {"image_ref": "ref"})

    import app.internal_client as client_module
    monkeypatch.setattr(client_module.httpx, "post", _fake_post)

    result = post_file("/internal/api/explore/stories/1/image", "image", "photo.png", b"bytes", "image/png")
    assert result == {"image_ref": "ref"}
    assert captured["files"]["image"] == ("photo.png", b"bytes", "image/png")

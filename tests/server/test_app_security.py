from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

from fastapi.testclient import TestClient

from ai_creation_canvas.app import _safe_static_file, create_app
from ai_creation_canvas.config import Settings


def signed_headers() -> dict[str, str]:
    timestamp = str(int(time.time()))
    payload = f"v2\n{timestamp}\nu-a\nuser\n{quote('Alice Example', safe='')}"
    signature = hmac.new(b"test-secret", payload.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Portal-Sig-Version": "2",
        "X-Portal-Timestamp": timestamp,
        "X-Portal-User-Id": "u-a",
        "X-Portal-Username": "Alice Example",
        "X-Portal-Role": "user",
        "X-Portal-Signature": signature,
    }


def make_client(tmp_path) -> TestClient:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>canvas</html>")
    (static_dir / "app.js").write_text("console.log('canvas')")
    settings = Settings(environment="test", port=8992, data_dir=tmp_path / "data", portal_internal_token="test-secret")
    return TestClient(create_app(settings, static_dir=static_dir), raise_server_exceptions=False)


def assert_security_headers(response) -> None:
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert "unsafe-eval" not in response.headers["content-security-policy"]


def test_session_exposes_only_verified_public_user_fields(tmp_path):
    client = make_client(tmp_path)
    response = client.get("/api/v1/session", headers=signed_headers())
    assert response.status_code == 200
    assert response.json() == {"user_id": "u-a", "username": "Alice Example", "role": "user"}
    assert_security_headers(response)
    assert "test-secret" not in response.text


def test_all_api_paths_require_identity_before_unknown_route_is_reported(tmp_path):
    client = make_client(tmp_path)
    response = client.get("/api/v1/not-real")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"
    assert_security_headers(response)

    authenticated = client.get("/api/v1/not-real", headers=signed_headers())
    assert authenticated.status_code == 404
    assert_security_headers(authenticated)


def test_options_and_head_api_identity_policy_is_explicit(tmp_path):
    client = make_client(tmp_path)
    assert client.options("/api/v1/session").status_code == 401
    assert client.head("/api/v1/session").status_code == 401
    assert client.options("/api/v1/session", headers=signed_headers()).status_code == 405
    assert client.head("/api/v1/session", headers=signed_headers()).status_code == 405


def test_spa_fallback_requires_html_get_and_does_not_hide_missing_assets(tmp_path):
    client = make_client(tmp_path)
    fallback = client.get("/canvas/board", headers={"accept": "text/html"})
    assert fallback.status_code == 200
    assert fallback.text == "<html>canvas</html>"
    assert_security_headers(fallback)
    assert client.get("/canvas/board", headers={"accept": "application/json"}).status_code == 404
    assert client.get("/missing.js", headers={"accept": "text/html"}).status_code == 404
    assert _safe_static_file(tmp_path / "dist", "../../etc/passwd") is None


def test_domain_and_unhandled_errors_use_safe_uniform_contract(tmp_path):
    client = make_client(tmp_path)
    response = client.get("/api/v1/session")
    body = response.json()
    assert set(body) == {"code", "message", "retryable", "request_id", "phase"}
    assert body["request_id"]
    assert "traceback" not in response.text.lower()
    invalid_request_id = client.get("/api/v1/session", headers={**signed_headers(), "X-Request-Id": "x" * 129})
    assert invalid_request_id.status_code == 200
    assert invalid_request_id.headers["x-request-id"] != "x" * 129

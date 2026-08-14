from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

from fastapi.testclient import TestClient

from ai_creation_canvas.app import _safe_static_file, create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.errors import ApiError, DomainError


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
    static_dir.mkdir(exist_ok=True)
    (static_dir / "index.html").write_text("<html>canvas</html>")
    (static_dir / "app.js").write_text("console.log('canvas')")
    settings = Settings(environment="test", port=8992, data_dir=tmp_path / "data", portal_internal_token="test-secret")
    return TestClient(create_app(settings, static_dir=static_dir), raise_server_exceptions=False)


def make_trusted_host_client(tmp_path) -> TestClient:
    static_dir = tmp_path / "dist"
    static_dir.mkdir(exist_ok=True)
    (static_dir / "index.html").write_text("<html>canvas</html>")
    settings = Settings(
        environment="test",
        port=8992,
        data_dir=tmp_path / "data",
        portal_internal_token="test-secret",
        trusted_hosts=("canvas.local",),
    )
    return TestClient(create_app(settings, static_dir=static_dir), raise_server_exceptions=False)


def assert_security_headers(response) -> None:
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert "unsafe-eval" not in response.headers["content-security-policy"]


def test_trusted_host_rejects_untrusted_host_even_when_forwarded_host_is_trusted(tmp_path):
    client = make_trusted_host_client(tmp_path)

    allowed = client.get("/healthz", headers={"host": "canvas.local"})
    assert allowed.status_code == 200

    rejected = client.get(
        "/healthz",
        headers={"host": "attacker.example", "x-forwarded-host": "canvas.local"},
    )
    assert rejected.status_code == 400


def test_healthz_is_unauthenticated_and_returns_fixed_status(tmp_path):
    client = make_client(tmp_path)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert_security_headers(response)


def test_readyz_is_unauthenticated_and_requires_a_safe_static_entrypoint(tmp_path):
    ready_client = make_client(tmp_path)
    ready = ready_client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert_security_headers(ready)

    missing_static_dir = tmp_path / "missing-dist"
    settings = Settings(
        environment="test",
        port=8992,
        data_dir=tmp_path / "missing-data",
        portal_internal_token="test-secret",
    )
    missing_static_client = TestClient(create_app(settings, static_dir=missing_static_dir), raise_server_exceptions=False)
    not_ready = missing_static_client.get("/readyz")
    assert not_ready.status_code == 503
    assert not_ready.json() == {"status": "not_ready"}
    assert_security_headers(not_ready)


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


def test_api_root_namespace_authenticates_before_its_json_not_found_response(tmp_path):
    client = make_client(tmp_path)
    for method in (client.get, client.head, client.options):
        assert method("/api/v1").status_code == 401
        assert method("/api/v1/").status_code == 401
        authenticated_root = method("/api/v1", headers=signed_headers())
        assert authenticated_root.status_code == 404
        assert authenticated_root.headers["content-type"].startswith("application/json")
        authenticated_slash = method("/api/v1/", headers=signed_headers())
        assert authenticated_slash.status_code == 404
        assert authenticated_slash.headers["content-type"].startswith("application/json")


def test_spa_fallback_requires_html_get_and_does_not_hide_missing_assets(tmp_path):
    client = make_client(tmp_path)
    fallback = client.get("/canvas/board", headers={"accept": "text/html"})
    assert fallback.status_code == 200
    assert fallback.text == "<html>canvas</html>"
    assert_security_headers(fallback)
    assert client.get("/canvas/board", headers={"accept": "application/json"}).status_code == 404
    assert client.get("/missing.js", headers={"accept": "text/html"}).status_code == 404
    assert _safe_static_file(tmp_path / "dist", "../../etc/passwd") is None


def test_outside_or_symlinked_static_paths_are_rejected_without_spa_fallback(tmp_path):
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    (external_dir / "route").write_text("outside")
    (static_dir / "outside-route").symlink_to(external_dir / "route")
    (static_dir / "nested").symlink_to(external_dir, target_is_directory=True)
    client = make_client(tmp_path)
    for path in ("/outside-route", "/nested/route"):
        response = client.get(path, headers={"accept": "text/html"})
        assert response.status_code == 404
        assert response.text != "<html>canvas</html>"


def test_all_static_symlinks_are_rejected_before_resolution_or_spa_fallback(tmp_path):
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "inside").write_text("inside")
    (static_dir / "inside-link").symlink_to("inside")
    real_directory = static_dir / "real-directory"
    real_directory.mkdir()
    (real_directory / "route").write_text("inside nested")
    (static_dir / "directory-link").symlink_to("real-directory", target_is_directory=True)
    (static_dir / "broken-link").symlink_to("not-present")
    (static_dir / "loop-one").symlink_to("loop-two")
    (static_dir / "loop-two").symlink_to("loop-one")

    client = make_client(tmp_path)
    for path in ("/inside-link", "/directory-link/route", "/broken-link", "/loop-one"):
        response = client.get(path, headers={"accept": "text/html"})
        assert response.status_code == 404
        assert response.text != "<html>canvas</html>"


def test_safe_missing_extensionless_route_can_use_spa_fallback(tmp_path):
    client = make_client(tmp_path)
    response = client.get("/missing-client-route", headers={"accept": "text/html"})
    assert response.status_code == 200
    assert response.text == "<html>canvas</html>"


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


def test_domain_error_response_uses_current_safe_request_id_not_exception_value(tmp_path):
    app = create_app(
        Settings(environment="test", port=8992, data_dir=tmp_path / "data", portal_internal_token="test-secret"),
        static_dir=tmp_path / "dist",
    )

    @app.get("/api/v1/test-domain-error")
    async def raise_domain_error():
        raise DomainError(ApiError("REQUEST_REJECTED", "Request rejected.", False, "secret\nrequest-id", "request"))

    app.router.routes.insert(0, app.router.routes.pop())

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/v1/test-domain-error", headers={**signed_headers(), "X-Request-Id": "safe-id"})
    assert response.status_code == 400
    assert response.json()["request_id"] == "safe-id"
    assert "secret" not in response.text

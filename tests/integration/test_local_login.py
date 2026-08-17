from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from ai_creation_canvas.api.auth import _register_rate_limiter
from ai_creation_canvas.app import create_app
from ai_creation_canvas.auth.local import BootstrapResult
from ai_creation_canvas.config import Settings
from ai_creation_canvas.storage.sqlite import CanvasStore


LOCAL_ORIGIN = "http://127.0.0.1:8992"


@dataclass(slots=True)
class LocalApp:
    client: TestClient
    store: CanvasStore
    accounts: BootstrapResult

    def login(self, username: str, password: str):
        return self.client.post("/api/v1/auth/login", json={"username": username, "password": password})


@pytest.fixture
def local_app(tmp_path) -> LocalApp:
    store = CanvasStore(tmp_path / "state")
    settings = Settings(
        environment="test",
        port=8992,
        data_dir=tmp_path / "state",
        portal_internal_token="local-test-signing-token-strong",
        identity_mode="local",
        allowed_origins=(LOCAL_ORIGIN,),
        session_ttl_seconds=600,
    )
    app = create_app(settings, static_dir=tmp_path / "missing-static", canvas_store=store)
    accounts = app.state.local_auth.bootstrap_accounts()
    return LocalApp(TestClient(app, base_url=LOCAL_ORIGIN), store, accounts)


def make_lan_client(local_app: LocalApp, origin: str) -> TestClient:
    settings = Settings(
        environment="test",
        port=8992,
        data_dir=local_app.store.data_dir,
        portal_internal_token="local-test-signing-token-strong",
        identity_mode="local",
        allowed_origins=(origin,),
        trusted_hosts=("192.168.1.20",),
        session_ttl_seconds=600,
    )
    return TestClient(create_app(settings, static_dir=local_app.store.data_dir / "missing-static", canvas_store=local_app.store), base_url=origin)


def mutation_headers(csrf_token: str, *, origin: str = LOCAL_ORIGIN) -> dict[str, str]:
    return {"X-CSRF-Token": csrf_token, "Origin": origin}


@pytest.fixture(autouse=True)
def reset_register_rate_limiter():
    _register_rate_limiter.reset()
    yield
    _register_rate_limiter.reset()


def test_login_cookie_session_csrf_and_logout(local_app: LocalApp) -> None:
    login = local_app.login(local_app.accounts.user_username, local_app.accounts.user_password)

    assert login.status_code == 200
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=lax" in login.headers["set-cookie"]
    body = login.json()
    assert body["user"]["role"] == "user"
    assert body["user"]["must_change_password"] is True
    csrf = body["csrf_token"]

    session = local_app.client.get("/api/v1/session")
    assert session.status_code == 200
    assert session.json()["csrf_token"] == csrf
    assert session.json()["user_id"] == body["user"]["user_id"]

    assert local_app.client.post("/api/v1/auth/logout").status_code == 403
    assert local_app.client.post(
        "/api/v1/auth/logout",
        headers=mutation_headers(csrf, origin="http://127.0.0.1:8993"),
    ).status_code == 403
    logout = local_app.client.post("/api/v1/auth/logout", headers=mutation_headers(csrf))
    assert logout.status_code == 204
    assert local_app.client.get("/api/v1/session").status_code == 401


def test_lan_origin_can_mutate_only_with_its_exact_csrf_origin(local_app: LocalApp) -> None:
    origin = "http://192.168.1.20:8992"
    client = make_lan_client(local_app, origin)
    login = client.post("/api/v1/auth/login", json={"username": local_app.accounts.user_username, "password": local_app.accounts.user_password})
    csrf = login.json()["csrf_token"]

    assert client.post("/api/v1/auth/logout", headers={"Origin": origin, "X-CSRF-Token": csrf}).status_code == 204


def test_unknown_disabled_and_wrong_password_have_same_public_failure(local_app: LocalApp) -> None:
    unknown = local_app.login("missing-user", "wrong-password-000")
    wrong = local_app.login(local_app.accounts.user_username, "wrong-password-000")
    assert unknown.status_code == wrong.status_code == 401
    public_fields = ("code", "message", "retryable", "phase")
    assert tuple(unknown.json()[field] for field in public_fields) == tuple(
        wrong.json()[field] for field in public_fields
    )

    assert local_app.accounts.user is not None
    local_app.store.set_user_enabled(local_app.accounts.user.user_id, False)
    disabled = local_app.login(local_app.accounts.user_username, local_app.accounts.user_password)
    assert disabled.status_code == 401
    assert tuple(disabled.json()[field] for field in public_fields) == tuple(
        wrong.json()[field] for field in public_fields
    )


def test_initial_password_change_rotates_session_and_password(local_app: LocalApp) -> None:
    login = local_app.login(local_app.accounts.user_username, local_app.accounts.user_password)
    old_cookie = local_app.client.cookies.get("aicc_session")
    old_csrf = login.json()["csrf_token"]

    changed = local_app.client.post(
        "/api/v1/auth/change-password",
        headers=mutation_headers(old_csrf),
        json={
            "current_password": local_app.accounts.user_password,
            "new_password": "new-correct-horse-battery",
        },
    )

    assert changed.status_code == 200
    assert changed.json()["csrf_token"] != old_csrf
    assert local_app.client.cookies.get("aicc_session") != old_cookie
    assert changed.json()["user"]["must_change_password"] is False

    local_app.client.post(
        "/api/v1/auth/logout",
        headers=mutation_headers(changed.json()["csrf_token"]),
    )
    assert local_app.login(local_app.accounts.user_username, local_app.accounts.user_password).status_code == 401
    assert local_app.login(local_app.accounts.user_username, "new-correct-horse-battery").status_code == 200


def test_local_mode_ignores_signed_identity_headers(local_app: LocalApp) -> None:
    response = local_app.client.get(
        "/api/v1/session",
        headers={
            "x-portal-user-id": "forged-user",
            "x-portal-username": "forged-admin",
            "x-portal-role": "admin",
            "x-portal-timestamp": "1",
            "x-portal-signature": "forged",
        },
    )
    assert response.status_code == 401


def test_register_returns_201_without_session_or_csrf_and_pending_user_cannot_login(local_app: LocalApp) -> None:
    register = local_app.client.post(
        "/api/v1/auth/register",
        json={"username": "newcomer", "display_name": "新同事", "password": "correct-horse-battery"},
    )

    assert register.status_code == 201
    assert register.json() == {"registered": True}

    pending = local_app.login("newcomer", "correct-horse-battery")
    wrong = local_app.login("newcomer", "wrong-password-000")
    assert pending.status_code == wrong.status_code == 401
    public_fields = ("code", "message", "retryable", "phase")
    assert tuple(pending.json()[field] for field in public_fields) == tuple(
        wrong.json()[field] for field in public_fields
    )


def test_register_duplicate_username_returns_409(local_app: LocalApp) -> None:
    local_app.client.post(
        "/api/v1/auth/register",
        json={"username": "newcomer", "display_name": "新同事", "password": "correct-horse-battery"},
    )

    for username in ("newcomer", " NEWCOMER ", local_app.accounts.user_username):
        duplicate = local_app.client.post(
            "/api/v1/auth/register",
            json={"username": username, "display_name": "另一个名字", "password": "correct-horse-battery"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "USERNAME_TAKEN"


def test_register_rejects_invalid_payloads(local_app: LocalApp) -> None:
    for payload in (
        {"username": "newcomer", "display_name": "名字", "password": "short"},
        {"username": "newcomer", "display_name": "", "password": "correct-horse-battery"},
        {"username": "", "display_name": "名字", "password": "correct-horse-battery"},
        {"username": "newcomer", "display_name": "名字", "password": "correct-horse-battery", "role": "admin"},
    ):
        rejected = local_app.client.post("/api/v1/auth/register", json=payload)
        assert rejected.status_code == 400
        assert rejected.json()["code"] == "REQUEST_REJECTED"


def test_register_is_rate_limited_per_ip(local_app: LocalApp) -> None:
    for index in range(10):
        payload = {"username": f"newcomer-{index}", "display_name": "新同事", "password": "correct-horse-battery"}
        assert local_app.client.post("/api/v1/auth/register", json=payload).status_code == 201
    limited = local_app.client.post(
        "/api/v1/auth/register",
        json={"username": "newcomer-late", "display_name": "新同事", "password": "correct-horse-battery"},
    )
    assert limited.status_code == 429
    assert limited.json()["code"] == "RATE_LIMITED"
    assert limited.json()["retryable"] is True

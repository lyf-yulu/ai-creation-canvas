from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.user_skin import DEFAULT_SKIN, SKIN_PRESETS, validate_skin


ORIGIN = "http://127.0.0.1:45996"


def _client(tmp_path: Path) -> TestClient:
    app = create_app(Settings("test", 45996, tmp_path / "data", "test-secret", identity_mode="local", allowed_origins=(ORIGIN,)), static_dir=tmp_path / "dist")
    app.state.local_auth.bootstrap_accounts(())
    return TestClient(app, base_url=ORIGIN, raise_server_exceptions=False)


def _login(client: TestClient) -> dict[str, str]:
    from ai_creation_canvas.auth.local import LocalAuthService
    from ai_creation_canvas.storage.sqlite import CanvasStore
    auth = LocalAuthService(CanvasStore(client.app.state.settings.data_dir), session_ttl_seconds=12 * 60 * 60)
    auth.create_user("skin-user", "Skin User", "skin-pass-123456", "user", must_change_password=False)
    response = client.post("/api/v1/auth/login", headers={"Origin": ORIGIN}, json={"username": "skin-user", "password": "skin-pass-123456"})
    assert response.status_code == 200, response.text
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


def test_session_exposes_default_skin_and_presets(tmp_path: Path) -> None:
    client = _client(tmp_path)
    headers = _login(client)
    response = client.get("/api/v1/session", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["skin"] == DEFAULT_SKIN
    assert set(body["skin_presets"]) == {"default", "monochrome", "classic-green"}


def test_user_skin_round_trip_and_validation(tmp_path: Path) -> None:
    client = _client(tmp_path)
    headers = _login(client)
    custom = {**DEFAULT_SKIN, "accent": "#ff0000", "bg": "#101010"}
    response = client.put("/api/v1/session/skin", headers=headers, json={"version": 1, "colors": custom})
    assert response.status_code == 200, response.text
    assert response.json()["skin"]["accent"] == "#ff0000"

    user_id = client.get("/api/v1/session", headers=headers).json()["user_id"]
    stored = client.app.state.canvas_store.user_skin(user_id)
    assert stored is not None and stored["bg"] == "#101010"

    session = client.get("/api/v1/session", headers=headers).json()
    assert session["skin"]["accent"] == "#ff0000"

    invalid = client.put("/api/v1/session/skin", headers=headers, json={"version": 1, "colors": {"accent": "rgb(1,2,3)"}})
    assert invalid.status_code == 400
    unknown = client.put("/api/v1/session/skin", headers=headers, json={"version": 1, "colors": {"shell_command": "#ff0000"}})
    assert unknown.status_code == 400


def test_skin_validation_module(tmp_path: Path) -> None:
    assert validate_skin({"version": 1, "colors": {"accent": "#2563eb"}})["accent"] == "#2563eb"
    with pytest.raises(ValueError):
        validate_skin({"version": 1, "colors": {"accent": "#fff"}})
    with pytest.raises(ValueError):
        validate_skin({"version": 2, "colors": {}})
    assert set(SKIN_PRESETS) == {"default", "monochrome", "classic-green"}
    assert json.loads(json.dumps(DEFAULT_SKIN)) == dict(DEFAULT_SKIN)

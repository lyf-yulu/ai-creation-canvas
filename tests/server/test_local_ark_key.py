"""Web-importable Ark generation key: wiring, summary, import and hot reload."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from ai_creation_canvas.__main__ import create_local_app
from ai_creation_canvas.adapters.ark import ArkGenerationAdapter


ORIGIN = "http://127.0.0.1:45994"
MODELS = Path(__file__).resolve().parents[2] / "server" / "config" / "ark-models.example.json"


def _key_document(api_key: str) -> dict[str, object]:
    return {"version": 1, "api_key": api_key}


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    first = client.post("/api/v1/auth/login", json={"username": username, "password": password}).json()
    changed = client.post(
        "/api/v1/auth/change-password",
        headers={"Origin": ORIGIN, "X-CSRF-Token": first["csrf_token"]},
        json={"current_password": password, "new_password": f"new-{username}-correct-horse"},
    ).json()
    return {"Origin": ORIGIN, "X-CSRF-Token": changed["csrf_token"]}


def _ark_adapters(app) -> tuple[ArkGenerationAdapter, ...]:
    return tuple(
        adapter for adapter in app.state.adapter_registry.generation_adapters()
        if isinstance(adapter, ArkGenerationAdapter)
    )


def test_ark_key_file_import_hot_reloads_adapters(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    root = tmp_path / "config"
    root.mkdir()
    key_path = root / "ark-key.json"
    key_path.write_text(json.dumps(_key_document("replace-with-api-key")), encoding="utf-8")
    key_path.chmod(0o600)

    app, accounts = create_local_app(
        port=45994,
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "dist",
        ark_models_config=MODELS,
        ark_key_config=key_path,
        ark_key_config_root=root,
        bootstrap_if_empty=True,
    )
    adapters = _ark_adapters(app)
    assert adapters
    assert all(adapter._api_key() == "replace-with-api-key" for adapter in adapters)

    admin = TestClient(app, base_url=ORIGIN)
    headers = _login(admin, accounts.admin_username, accounts.admin_password)

    summary = admin.get("/api/v1/admin/ark-key", headers=headers)
    assert summary.status_code == 200
    assert summary.json() == {"configured": True, "has_key": True}

    imported = admin.post(
        "/api/v1/admin/ark-key/import",
        headers=headers,
        files={"file": ("ark-key.json", json.dumps(_key_document("real-ark-key-12345")).encode("utf-8"), "application/json")},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json() == {"configured": True, "has_key": True}
    assert all(adapter._api_key() == "real-ark-key-12345" for adapter in adapters)

    rejected = admin.post(
        "/api/v1/admin/ark-key/import",
        headers=headers,
        files={"file": ("ark-key.json", json.dumps({"version": 2, "api_key": "real-ark-key-12345"}).encode("utf-8"), "application/json")},
    )
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "ARK_KEY_INVALID"


def test_ark_key_endpoints_report_unconfigured_without_the_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    app, accounts = create_local_app(
        port=45994,
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "dist",
        bootstrap_if_empty=True,
    )
    admin = TestClient(app, base_url=ORIGIN)
    headers = _login(admin, accounts.admin_username, accounts.admin_password)
    summary = admin.get("/api/v1/admin/ark-key", headers=headers)
    assert summary.status_code == 200
    assert summary.json() == {"configured": False, "has_key": False}


def test_config_example_downloads_are_admin_only_and_valid(tmp_path: Path) -> None:
    app, accounts = create_local_app(
        port=45994,
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "dist",
        bootstrap_if_empty=True,
    )
    admin = TestClient(app, base_url=ORIGIN)
    headers = _login(admin, accounts.admin_username, accounts.admin_password)
    for kind in ("ark-key", "credential-pools", "asset-library"):
        response = admin.get(f"/api/v1/admin/config-examples/{kind}", headers=headers)
        assert response.status_code == 200, kind
        assert response.headers["content-disposition"] == f'attachment; filename="{kind}.example.json"'
        assert isinstance(response.json(), dict)
    assert admin.get("/api/v1/admin/config-examples/unknown", headers=headers).status_code == 404
    anonymous = TestClient(app, base_url=ORIGIN)
    assert anonymous.get("/api/v1/admin/config-examples/ark-key").status_code != 200


def test_ark_models_still_require_a_key_without_any_key_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    try:
        create_local_app(
            port=45996,
            data_dir=tmp_path / "data",
            static_dir=tmp_path / "dist",
            ark_models_config=MODELS,
            bootstrap_if_empty=False,
        )
    except ValueError as error:
        assert "ARK_API_KEY is required" in str(error)
    else:
        raise AssertionError("missing Ark key must fail startup without a key source")

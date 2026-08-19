"""Serve-local credential pools wiring: providers, runtime, import and summaries."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from ai_creation_canvas.__main__ import create_local_app


ORIGIN = "http://127.0.0.1:45993"

EXAMPLE_POOLS = {
    "version": 1,
    "pools": {
        "seedream-ark": {
            "provider": "ark",
            "group": "official",
            "allowed_families": ["seedream"],
            "keys": [{"id": "seedream-primary", "api_key": "replace-with-provider-key", "max_concurrency": 1}],
        },
        "seedance-ark": {
            "provider": "ark",
            "group": "official",
            "allowed_families": ["seedance"],
            "keys": [{"id": "seedance-primary", "api_key": "replace-with-provider-key", "max_concurrency": 1}],
        },
    },
}


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    first = client.post("/api/v1/auth/login", json={"username": username, "password": password}).json()
    changed = client.post(
        "/api/v1/auth/change-password",
        headers={"Origin": ORIGIN, "X-CSRF-Token": first["csrf_token"]},
        json={"current_password": password, "new_password": f"new-{username}-correct-horse"},
    ).json()
    return {"Origin": ORIGIN, "X-CSRF-Token": changed["csrf_token"]}


def test_credential_pools_and_asset_library_can_be_configured_together(tmp_path: Path) -> None:
    root = tmp_path / "config"
    root.mkdir()
    pools_path = root / "credential-pools.json"
    pools_path.write_text(json.dumps(EXAMPLE_POOLS), encoding="utf-8")
    pools_path.chmod(0o600)
    asset_path = root / "asset-library.json"
    asset_path.write_text(json.dumps({
        "version": 1,
        "ark_access_key": "replace-with-ark-access-key",
        "ark_secret_key": "replace-with-ark-secret-key",
        "tos_access_key": "replace-with-tos-access-key",
        "tos_secret_key": "replace-with-tos-secret-key",
        "tos_bucket": "replace-with-tos-bucket",
        "tos_region": "cn-beijing",
        "project_name": "Seedance2.0",
    }), encoding="utf-8")
    asset_path.chmod(0o600)

    app, _ = create_local_app(
        port=45993,
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "dist",
        credential_pools=pools_path,
        credential_pools_root=root,
        asset_library_config=asset_path,
        asset_library_config_root=root,
        bootstrap_if_empty=True,
    )
    runtime = app.state.managed_routing_runtime
    assert runtime is not None
    assert {item for item in runtime.pools()} == {"seedream-ark", "seedance-ark"}


def test_serve_local_with_credential_pools_wires_runtime_providers_and_import(tmp_path: Path) -> None:
    root = tmp_path / "config"
    root.mkdir()
    pools_path = root / "credential-pools.json"
    pools_path.write_text(json.dumps(EXAMPLE_POOLS), encoding="utf-8")
    pools_path.chmod(0o600)

    app, accounts = create_local_app(
        port=45993,
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "dist",
        credential_pools=pools_path,
        credential_pools_root=root,
        bootstrap_if_empty=True,
    )
    assert app.state.managed_routing_runtime is not None
    assert {item.provider_id for item in app.state.canvas_store.list_provider_definitions()} == {
        "chiyun-banana",
        "chiyun-gpt-image2",
        "ark",
    }

    admin = TestClient(app, base_url=ORIGIN)
    headers = _login(admin, accounts.admin_username, accounts.admin_password)

    listed = admin.get("/api/v1/admin/credential-pools", headers=headers)
    assert listed.status_code == 200
    assert {item["pool_id"] for item in listed.json()["pools"]} == {"seedream-ark", "seedance-ark"}

    updated = dict(EXAMPLE_POOLS)
    updated["pools"]["seedream-ark"]["keys"][0]["max_concurrency"] = 2
    imported = admin.post(
        "/api/v1/admin/credential-pools/import",
        headers=headers,
        files={"file": ("pools.json", json.dumps(updated).encode("utf-8"), "application/json")},
    )
    assert imported.status_code == 200, imported.text
    refreshed = admin.get("/api/v1/admin/credential-pools", headers=headers).json()["pools"]
    seedream = next(item for item in refreshed if item["pool_id"] == "seedream-ark")
    assert seedream["total_capacity"] == 2

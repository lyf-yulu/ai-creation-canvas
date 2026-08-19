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


def test_import_parsers_tolerate_a_utf8_bom() -> None:
    """Windows Notepad saves UTF-8 files with a BOM; imports must still parse."""
    from ai_creation_canvas.ark_key_config import parse_ark_key_config_json
    from ai_creation_canvas.asset_library_config import parse_asset_library_config_json
    from ai_creation_canvas.comfy.workflow_json import parse_workflow_json
    from ai_creation_canvas.credential_pools import parse_credential_pool_json
    bom = "﻿"
    parse_credential_pool_json((bom + json.dumps(EXAMPLE_POOLS)).encode("utf-8"))
    parse_ark_key_config_json((bom + json.dumps({"version": 1, "api_key": "replace-with-api-key"})).encode("utf-8"))
    parse_asset_library_config_json((bom + json.dumps({
        "version": 1,
        "ark_access_key": "replace-with-ark-access-key",
        "ark_secret_key": "replace-with-ark-secret-key",
        "tos_access_key": "replace-with-tos-access-key",
        "tos_secret_key": "replace-with-tos-secret-key",
        "tos_bucket": "replace-with-tos-bucket",
        "tos_region": "cn-beijing",
        "project_name": "Seedance2.0",
    })).encode("utf-8"))
    workflow = Path(__file__).resolve().parents[2] / "server" / "config" / "comfy-workflow.example.json"
    parse_workflow_json((bom + workflow.read_text(encoding="utf-8")).encode("utf-8"))


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

    # Windows browsers often report .json files as octet-stream; accept them too.
    octet = admin.post(
        "/api/v1/admin/credential-pools/import",
        headers=headers,
        files={"file": ("pools.json", json.dumps(EXAMPLE_POOLS).encode("utf-8"), "application/octet-stream")},
    )
    assert octet.status_code == 200, octet.text

    # Unsupported provider combinations report a readable reason.
    bad = dict(EXAMPLE_POOLS)
    bad["pools"]["seedream-ark"]["provider"] = "unknown-provider"
    rejected = admin.post(
        "/api/v1/admin/credential-pools/import",
        headers=headers,
        files={"file": ("pools.json", json.dumps(bad).encode("utf-8"), "application/json")},
    )
    assert rejected.status_code == 400
    assert "组合不受支持" in rejected.json()["message"]

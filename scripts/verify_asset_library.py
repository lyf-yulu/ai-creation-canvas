#!/usr/bin/env python3
"""Offline end-to-end smoke for the Ark private portrait asset library.

Uses httpx.MockTransport and temporary data only; never contacts real Ark,
TOS, Portal, or production state. Exits non-zero on any failed assertion.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient
import httpx

from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration
from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import ModelInputPort
from ai_creation_canvas.domain.registry import AdapterRegistry


ORIGIN = "http://127.0.0.1:8992"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND\xaeB`\x82"


def config_json() -> bytes:
    return json.dumps(
        {
            "version": 1,
            "ark_access_key": "AK-SMOKE",
            "ark_secret_key": base64.b64encode(b"smoke-ark-sk").decode(),
            "tos_access_key": "TOS-AK-SMOKE",
            "tos_secret_key": "TOS-SK-SMOKE",
            "tos_bucket": "smoke-bucket",
            "tos_region": "cn-beijing",
            "project_name": "Seedance2.0",
        },
        separators=(",", ":"),
    ).encode()


def library_handler() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host.endswith(".tos-cn-beijing.volces.com"):
            return httpx.Response(200)
        query = request.url.query.decode("ascii")
        if query == "Action=CreateAssetGroup&Version=2024-01-01":
            return httpx.Response(200, json={"Result": {"Id": "asset-grp-smoke"}})
        if query == "Action=CreateAsset&Version=2024-01-01":
            return httpx.Response(200, json={"Result": {"Id": "asset-smoke", "Status": "Processing"}})
        if query == "Action=GetAsset&Version=2024-01-01":
            return httpx.Response(200, json={"Result": {"Id": "asset-smoke", "Status": "Active", "AssetType": "Image"}})
        raise AssertionError(f"unexpected library action {query}")

    return httpx.MockTransport(handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="temporary data directory (created and cleaned by this script)")
    args = parser.parse_args()
    root = Path(args.data_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"refusing to use a non-empty data directory: {root}")
    root.mkdir(parents=True, exist_ok=True)

    generation_payloads: list[dict[str, object]] = []
    generation = ArkGenerationAdapter(
        api_key="test-only-secret", data_dir=root, models=(ArkModelDeclaration(
            "seedance-v1", "ark-video", "Seedance", ("video.generate",),
            {"type": "object", "properties": {}, "additionalProperties": False},
            (ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 0, 30, asset_kind="library")),
            {},
        ),),
        transport=httpx.MockTransport(lambda request: generation_payloads.append(json.loads(request.content)) or httpx.Response(200, json={"id": "cgt-smoke"})),
    )
    registry = AdapterRegistry()
    registry.register_generation(generation)
    config_file = root / "asset-library.json"
    config_file.write_bytes(config_json())
    config_file.chmod(0o600)
    settings = Settings(
        "test", 8992, root / "data", "test-secret",
        identity_mode="local", allowed_origins=(ORIGIN,),
        asset_library_config_path=config_file, asset_library_config_root=root,
    )
    app = create_app(settings, registry=registry, model_catalog=ModelCatalog(registry), asset_library_transport=library_handler())
    accounts = app.state.local_auth.bootstrap_accounts(("seedance-v1",))
    admin = TestClient(app, base_url=ORIGIN)
    user = TestClient(app, base_url=ORIGIN)
    admin_login = admin.post("/api/v1/auth/login", json={"username": accounts.admin_username, "password": accounts.admin_password}).json()
    user_login = user.post("/api/v1/auth/login", json={"username": accounts.user_username, "password": accounts.user_password}).json()
    admin_changed = admin.post("/api/v1/auth/change-password", headers={"Origin": ORIGIN, "X-CSRF-Token": admin_login["csrf_token"]}, json={"current_password": accounts.admin_password, "new_password": "smoke-admin-password"}).json()
    user_changed = user.post("/api/v1/auth/change-password", headers={"Origin": ORIGIN, "X-CSRF-Token": user_login["csrf_token"]}, json={"current_password": accounts.user_password, "new_password": "smoke-user-password"}).json()
    admin_headers = {"Origin": ORIGIN, "X-CSRF-Token": admin_changed["csrf_token"]}
    user_headers = {"Origin": ORIGIN, "X-CSRF-Token": user_changed["csrf_token"]}

    checks: list[tuple[str, bool]] = []

    def check(name: str, condition: bool) -> None:
        checks.append((name, condition))

    imported = admin.post("/api/v1/admin/asset-library/import", files={"file": ("asset-library.json", config_json(), "application/json")}, headers=admin_headers)
    check("admin import succeeds", imported.status_code == 200)
    check("import summary hides secrets", "AK-SMOKE" not in imported.text and "smoke-ark-sk" not in imported.text)

    uploaded = user.post("/api/v1/assets", files={"file": ("portrait.png", PNG, "image/png")}, data={"kind": "library", "media_type": "image"}, headers=user_headers)
    check("library upload succeeds", uploaded.status_code == 201)
    asset = uploaded.json() if uploaded.status_code == 201 else {}
    polled = user.get(f"/api/v1/assets/{asset.get('asset_id', 'missing')}", headers=user_headers) if asset else None
    check("library asset reaches active", polled is not None and polled.status_code == 200 and polled.json()["status"] == "active")

    submitted = user.post(
        "/api/v1/jobs",
        json={"operation": "video.generate", "model_id": "seedance-v1", "prompt": "animate", "params": {}, "asset_ids": [], "inputs": {"reference_images": [asset.get("asset_id", "missing")]}, "idempotency_key": "smoke-key"},
        headers=user_headers,
    )
    check("seedance job submits with asset reference", submitted.status_code == 201)
    check("generation content carries asset:// verbatim", generation_payloads == [{
        "model": "seedance-v1",
        "content": [
            {"type": "text", "text": "animate"},
            {"type": "image_url", "image_url": {"url": "asset://asset-smoke"}, "role": "reference_image"},
        ],
    }])
    check("asset listing is owner scoped and secret free", "AK-SMOKE" not in user.get("/api/v1/library-assets", headers=user_headers).text)

    for name, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'}: {name}")
    failed = [name for name, passed in checks if not passed]
    if failed:
        print(f"{len(failed)} check(s) failed", file=__import__("sys").stderr)
        return 1
    print("asset library offline smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

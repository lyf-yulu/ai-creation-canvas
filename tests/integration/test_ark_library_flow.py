from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx

from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration
from ai_creation_canvas.adapters.ark_assets import ArkAssetLibraryAdapter
from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.asset_library_config import AssetLibraryConfig
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import ModelInputPort
from ai_creation_canvas.domain.registry import AdapterRegistry
from tests.contracts.test_generation_flow import headers


ORIGIN = "http://127.0.0.1:8992"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND\xaeB`\x82"


def config_json(*, project_name: str = "Seedance2.0", bucket: str = "canvas-uploads") -> bytes:
    return json.dumps(
        {
            "version": 1,
            "ark_access_key": "AK-TEST",
            "ark_secret_key": base64.b64encode(b"decoded-ark-sk").decode(),
            "tos_access_key": "TOS-AK-TEST",
            "tos_secret_key": "TOS-SK-TEST",
            "tos_bucket": bucket,
            "tos_region": "cn-beijing",
            "project_name": project_name,
        },
        separators=(",", ":"),
    ).encode()


def library_handler(requests: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host.endswith(".tos-cn-beijing.volces.com"):
            return httpx.Response(200)
        query = request.url.query.decode("ascii")
        if query == "Action=CreateAssetGroup&Version=2024-01-01":
            return httpx.Response(200, json={"Result": {"Id": "asset-grp-new"}})
        if query == "Action=CreateAsset&Version=2024-01-01":
            return httpx.Response(200, json={"Result": {"Id": "asset-abc", "Status": "Processing"}})
        if query == "Action=GetAsset&Version=2024-01-01":
            return httpx.Response(200, json={"Result": {"Id": "asset-abc", "Status": "Active", "AssetType": "Image"}})
        raise AssertionError(f"unexpected library action {query}")

    return handler


def seedance_declaration() -> ArkModelDeclaration:
    return ArkModelDeclaration(
        "seedance-v1", "ark-video", "Seedance", ("video.generate",),
        {"type": "object", "properties": {}, "additionalProperties": False},
        (
            ModelInputPort("prompt", "text", 1, 1),
            ModelInputPort("reference_images", "image", 0, 30, asset_kind="library"),
        ),
        {},
    )


def test_library_upload_to_seedance_job_round_trip_with_asset_reference(tmp_path: Path) -> None:
    """Admin imports the company config, a user uploads a portrait, it becomes
    active, and a Seedance job carries the asset:// reference verbatim."""
    generation_payloads: list[dict[str, object]] = []

    def generation_handler(request: httpx.Request) -> httpx.Response:
        generation_payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "cgt-lib-1"})

    generation = ArkGenerationAdapter(
        api_key="test-only-secret", data_dir=tmp_path / "data", models=(seedance_declaration(),),
        transport=httpx.MockTransport(generation_handler),
    )
    registry = AdapterRegistry()
    registry.register_generation(generation)
    config_file = tmp_path / "asset-library.json"
    config_file.write_bytes(config_json())
    config_file.chmod(0o600)
    settings = Settings(
        "test", 8992, tmp_path / "data", "test-secret",
        identity_mode="local", allowed_origins=(ORIGIN,),
        asset_library_config_path=config_file, asset_library_config_root=tmp_path,
    )
    app = create_app(
        settings, registry=registry, model_catalog=ModelCatalog(registry),
        asset_library_transport=httpx.MockTransport(library_handler([])),
    )
    accounts = app.state.local_auth.bootstrap_accounts(("seedance-v1",))
    admin = TestClient(app, base_url=ORIGIN)
    user = TestClient(app, base_url=ORIGIN)
    admin_login = admin.post("/api/v1/auth/login", json={"username": accounts.admin_username, "password": accounts.admin_password}).json()
    user_login = user.post("/api/v1/auth/login", json={"username": accounts.user_username, "password": accounts.user_password}).json()
    admin_changed = admin.post(
        "/api/v1/auth/change-password",
        headers={"Origin": ORIGIN, "X-CSRF-Token": admin_login["csrf_token"]},
        json={"current_password": accounts.admin_password, "new_password": "new-admin-correct-horse"},
    ).json()
    user_changed = user.post(
        "/api/v1/auth/change-password",
        headers={"Origin": ORIGIN, "X-CSRF-Token": user_login["csrf_token"]},
        json={"current_password": accounts.user_password, "new_password": "new-user-correct-horse"},
    ).json()
    admin_headers = {"Origin": ORIGIN, "X-CSRF-Token": admin_changed["csrf_token"]}
    user_headers = {"Origin": ORIGIN, "X-CSRF-Token": user_changed["csrf_token"]}

    imported = admin.post(
        "/api/v1/admin/asset-library/import",
        files={"file": ("asset-library.json", config_json(project_name="RotatedProject", bucket="rotated-bucket"), "application/json")},
        headers=admin_headers,
    )
    assert imported.status_code == 200
    assert imported.json()["tos_bucket"] == "rotated-bucket"
    assert "AK-TEST" not in imported.text and "decoded-ark-sk" not in imported.text

    uploaded = user.post(
        "/api/v1/assets",
        files={"file": ("portrait.png", PNG, "image/png")},
        data={"kind": "library", "media_type": "image"},
        headers=user_headers,
    )
    assert uploaded.status_code == 201
    asset = uploaded.json()
    assert asset["kind"] == "library" and asset["status"] in {"processing", "active"}

    polled = user.get(f"/api/v1/assets/{asset['asset_id']}", headers=user_headers)
    assert polled.status_code == 200 and polled.json()["status"] == "active"

    submitted = user.post(
        "/api/v1/jobs",
        json={
            "operation": "video.generate",
            "model_id": "seedance-v1",
            "prompt": "animate the portrait",
            "params": {},
            "asset_ids": [],
            "inputs": {"reference_images": [asset["asset_id"]]},
            "idempotency_key": "integration-key-1",
        },
        headers=user_headers,
    )
    assert submitted.status_code == 201
    assert generation_payloads == [{
        "model": "seedance-v1",
        "content": [
            {"type": "text", "text": "animate the portrait"},
            {"type": "image_url", "image_url": {"url": "asset://asset-abc"}, "role": "reference_image"},
        ],
    }]

    listed = user.get("/api/v1/library-assets", headers=user_headers).json()["assets"]
    assert [item["asset_id"] for item in listed] == [asset["asset_id"]]
    assert "AK-TEST" not in json.dumps(listed) and "decoded-ark-sk" not in json.dumps(listed)
    assert b"AK-TEST" not in (tmp_path / "data" / "canvas.sqlite3").read_bytes()


def test_library_assets_are_isolated_between_users(tmp_path: Path) -> None:
    """Cross-user reads and job submissions against another user's library asset
    are rejected through the signed-portal identity surface."""
    from ai_creation_canvas.asset_library_config import AssetLibraryConfig

    library_requests: list[httpx.Request] = []
    service = ArkAssetLibraryAdapter(
        config=AssetLibraryConfig(
            ark_access_key="AK-TEST", ark_secret_key="SK-TEST-0123456789",
            tos_access_key="TOS-AK-TEST", tos_secret_key="TOS-SK-TEST",
            tos_bucket="canvas-uploads", tos_region="cn-beijing", project_name="Seedance2.0",
        ),
        group_id_getter=lambda: "asset-grp-1",
        group_id_setter=lambda gid: None,
        transport=httpx.MockTransport(library_handler(library_requests)),
        get_asset_attempts=0,
        get_asset_interval=0.0,
    )
    generation_payloads: list[dict[str, object]] = []
    generation = ArkGenerationAdapter(
        api_key="test-only-secret", data_dir=tmp_path / "data", models=(seedance_declaration(),),
        transport=httpx.MockTransport(lambda request: generation_payloads.append(json.loads(request.content)) or httpx.Response(200, json={"id": "cgt-lib-2"})),
    )
    registry = AdapterRegistry()
    registry.register_generation(generation)
    settings = Settings("test", 8992, tmp_path / "data", "test-secret")
    app = create_app(settings, registry=registry, model_catalog=ModelCatalog(registry), asset_library_service=service)
    client = TestClient(app, raise_server_exceptions=False)

    uploaded = client.post(
        "/api/v1/assets",
        files={"file": ("portrait.png", PNG, "image/png")},
        data={"kind": "library", "media_type": "image"},
        headers=headers(user="u-a"),
    )
    asset = uploaded.json()

    forbidden_read = client.get(f"/api/v1/assets/{asset['asset_id']}", headers=headers(user="u-b"))
    assert forbidden_read.status_code == 403

    forbidden_job = client.post(
        "/api/v1/jobs",
        json={
            "operation": "video.generate",
            "model_id": "seedance-v1",
            "prompt": "animate",
            "params": {},
            "asset_ids": [],
            "inputs": {"reference_images": [asset["asset_id"]]},
            "idempotency_key": "integration-key-2",
        },
        headers=headers(user="u-b"),
    )
    assert forbidden_job.status_code == 403
    assert generation_payloads == []

from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest

from ai_creation_canvas.adapters.ark_assets import ArkAssetLibraryAdapter
from ai_creation_canvas.asset_library_config import AssetLibraryConfig
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.registry import AdapterRegistry
from tests.contracts.test_generation_flow import FakeGeneration, headers


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND\xaeB`\x82"


def library_config() -> AssetLibraryConfig:
    return AssetLibraryConfig(
        ark_access_key="AK-TEST", ark_secret_key="SK-TEST-0123456789",
        tos_access_key="TOS-AK-TEST", tos_secret_key="TOS-SK-TEST",
        tos_bucket="canvas-uploads", tos_region="cn-beijing", project_name="Seedance2.0",
    )


def library_adapter(handler) -> ArkAssetLibraryAdapter:
    return ArkAssetLibraryAdapter(
        config=library_config(),
        group_id_getter=lambda: "asset-grp-1",
        group_id_setter=lambda gid: None,
        transport=httpx.MockTransport(handler),
        get_asset_attempts=0,
        get_asset_interval=0.0,
    )


def library_upload_handler(requests: list[httpx.Request], *, create_status: str = "Processing", get_status: str = "Active"):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host.startswith("canvas-uploads"):
            return httpx.Response(200)
        if request.url.query.decode("ascii") == "Action=CreateAsset&Version=2024-01-01":
            return httpx.Response(200, json={"Result": {"Id": "asset-abc", "Status": create_status}})
        return httpx.Response(200, json={"Result": {"Id": "asset-abc", "Status": get_status}})

    return handler


def configured_settings(tmp_path: Path, *, config_file: Path | None = None) -> Settings:
    return Settings(
        "test", 8992, tmp_path / "data", "test-secret",
        max_image_upload_bytes=4096,
        asset_library_config_path=config_file,
        asset_library_config_root=tmp_path if config_file is not None else None,
    )


def make_client(tmp_path: Path, settings: Settings, service=None) -> TestClient:
    registry = AdapterRegistry()
    registry.register_generation(FakeGeneration())
    app = create_app(settings, registry=registry, asset_library_service=service)
    return TestClient(app, raise_server_exceptions=False)


def test_library_upload_requires_configured_service(tmp_path: Path) -> None:
    client = make_client(tmp_path, configured_settings(tmp_path))

    response = client.post(
        "/api/v1/assets",
        files={"file": ("portrait.png", PNG, "image/png")},
        data={"kind": "library", "media_type": "image"},
        headers=headers(),
    )

    assert response.status_code == 503
    assert response.json()["code"] == "LIBRARY_ASSETS_UNAVAILABLE"
    assert "AK" not in response.text and "secret" not in response.text.lower()


def test_library_upload_polls_to_active_via_get(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = make_client(tmp_path, configured_settings(tmp_path), library_adapter(library_upload_handler(requests)))

    response = client.post(
        "/api/v1/assets",
        files={"file": ("portrait.png", PNG, "image/png")},
        data={"kind": "library", "media_type": "image"},
        headers=headers(),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "library" and body["status"] == "processing"

    response = client.get(f"/api/v1/assets/{body['asset_id']}", headers=headers())
    assert response.status_code == 200
    assert response.json()["status"] == "active"

    forbidden = client.get(f"/api/v1/assets/{body['asset_id']}", headers=headers(user="u-b"))
    assert forbidden.status_code == 403
    assert [request.url.host for request in requests][:2] == [
        "canvas-uploads.tos-cn-beijing.volces.com",
        "ark.cn-beijing.volcengineapi.com",
    ]
    assert requests[-1].url.query.decode("ascii") == "Action=GetAsset&Version=2024-01-01"


def test_library_upload_rejects_non_image(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = make_client(tmp_path, configured_settings(tmp_path), library_adapter(library_upload_handler(requests)))

    response = client.post(
        "/api/v1/assets",
        files={"file": ("clip.mp4", b"\x00\x00\x00\x18ftypisom", "video/mp4")},
        data={"kind": "library", "media_type": "video"},
        headers=headers(),
    )
    assert response.status_code == 400
    assert requests == []


def test_library_upload_enforces_image_limit(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = make_client(tmp_path, configured_settings(tmp_path), library_adapter(library_upload_handler(requests)))

    response = client.post(
        "/api/v1/assets",
        files={"file": ("portrait.png", PNG + b"\x00" * 4096, "image/png")},
        data={"kind": "library", "media_type": "image"},
        headers=headers(),
    )
    assert response.status_code == 413
    assert requests == []


def test_library_upload_cleans_reservation_on_upstream_failure(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host.startswith("canvas-uploads"):
            return httpx.Response(500)
        raise AssertionError("no further upstream calls expected")

    settings = configured_settings(tmp_path)
    client = make_client(tmp_path, settings, library_adapter(handler))

    response = client.post(
        "/api/v1/assets",
        files={"file": ("portrait.png", PNG, "image/png")},
        data={"kind": "library", "media_type": "image"},
        headers=headers(),
    )
    assert response.status_code == 502 and response.json()["code"] == "UPSTREAM_UNAVAILABLE"

    from ai_creation_canvas.storage.sqlite import CanvasStore

    store = CanvasStore(settings.data_dir)
    assert store.list_library_assets_for_owner("u-a") == ()


def test_library_assets_listing_is_owner_scoped(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    client = make_client(tmp_path, configured_settings(tmp_path), library_adapter(library_upload_handler(requests)))
    owned = client.post(
        "/api/v1/assets",
        files={"file": ("portrait.png", PNG, "image/png")},
        data={"kind": "library", "media_type": "image"},
        headers=headers(),
    ).json()

    response = client.get("/api/v1/library-assets", headers=headers())
    assert response.status_code == 200
    assert [item["asset_id"] for item in response.json()["assets"]] == [owned["asset_id"]]
    assert response.json()["assets"][0]["kind"] == "library"
    other = client.get("/api/v1/library-assets", headers=headers(user="u-b"))
    assert other.status_code == 200 and other.json()["assets"] == []


def admin_json() -> bytes:
    return json.dumps(
        {
            "version": 1,
            "ark_access_key": "AK-NEW",
            "ark_secret_key": base64.b64encode(b"new-ark-sk").decode(),
            "tos_access_key": "TOS-AK-NEW",
            "tos_secret_key": "TOS-SK-NEW",
            "tos_bucket": "new-bucket",
            "tos_region": "cn-beijing",
            "project_name": "Seedance2.0",
        },
        separators=(",", ":"),
    ).encode()


ORIGIN = "http://127.0.0.1:8992"


def local_clients(tmp_path: Path, *, config_file: Path | None = None, service=None) -> tuple[TestClient, TestClient, dict[str, str]]:
    """Admin API is only served in local identity mode (repo policy)."""
    registry = AdapterRegistry()
    registry.register_generation(FakeGeneration())
    settings = Settings(
        "test", 8992, tmp_path / "data", "test-secret",
        identity_mode="local", allowed_origins=(ORIGIN,),
        max_image_upload_bytes=4096,
        asset_library_config_path=config_file,
        asset_library_config_root=tmp_path if config_file is not None else None,
    )
    app = create_app(settings, registry=registry, asset_library_service=service)
    accounts = app.state.local_auth.bootstrap_accounts(("demo-image-v1",))
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
    return admin, user, admin_headers, user_headers


def test_admin_asset_library_summary_never_echoes_secrets(tmp_path: Path) -> None:
    config_file = tmp_path / "asset-library.json"
    config_file.write_bytes(admin_json())
    config_file.chmod(0o600)
    admin, user, admin_headers, _ = local_clients(tmp_path, config_file=config_file)

    response = admin.get("/api/v1/admin/asset-library", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True and body["import_configured"] is True
    assert body["has_ark_access"] is True and body["tos_bucket"] == "new-bucket"
    encoded = response.text
    assert "AK-NEW" not in encoded and "new-ark-sk" not in encoded and "TOS-SK-NEW" not in encoded

    hidden = user.get("/api/v1/admin/asset-library")
    assert hidden.status_code == 404


def test_admin_asset_library_import_replaces_config_atomically(tmp_path: Path) -> None:
    config_file = tmp_path / "asset-library.json"
    config_file.write_bytes(admin_json())
    config_file.chmod(0o600)
    admin, _, admin_headers, _ = local_clients(tmp_path, config_file=config_file)

    response = admin.post(
        "/api/v1/admin/asset-library/import",
        files={"file": ("asset-library.json", admin_json().replace(b"new-bucket", b"rotated-bucket"), "application/json")},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["tos_bucket"] == "rotated-bucket"
    assert "AK-NEW" not in response.text
    assert b"rotated-bucket" in config_file.read_bytes()

    bad = admin.post(
        "/api/v1/admin/asset-library/import",
        files={"file": ("asset-library.json", b'{"version":1,"version":1}', "application/json")},
        headers=admin_headers,
    )
    assert bad.status_code == 400


def test_admin_asset_library_import_unconfigured_returns_409(tmp_path: Path) -> None:
    admin, _, admin_headers, _ = local_clients(tmp_path)

    response = admin.post(
        "/api/v1/admin/asset-library/import",
        files={"file": ("asset-library.json", admin_json(), "application/json")},
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "ASSET_LIBRARY_IMPORT_UNAVAILABLE"


def test_admin_asset_library_groups(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"Result": {"Items": [{"Id": "asset-grp-1", "Name": "Default"}]}})

    admin, _, admin_headers, _ = local_clients(tmp_path, service=library_adapter(handler))

    response = admin.get("/api/v1/admin/asset-library/groups", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == {"groups": [{"group_id": "asset-grp-1", "name": "Default"}]}
    assert json.loads(requests[0].content)["Filter"]["GroupType"] == "AIGC"

    unconfigured, _, unconfigured_headers, _ = local_clients(tmp_path / "unconfigured")
    missing = unconfigured.get("/api/v1/admin/asset-library/groups", headers=unconfigured_headers)
    assert missing.status_code == 409 and missing.json()["code"] == "ASSET_LIBRARY_UNAVAILABLE"

from __future__ import annotations

import base64
import asyncio
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.factory import AdapterFactory, MappingCredentialResolver
from ai_creation_canvas.adapters.ark import _local_asset_loader
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.storage.sqlite import CanvasStore


ORIGIN = "http://127.0.0.1:8996"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND\xaeB`\x82"
OUTPUT = b"\x89PNG\r\n\x1a\n" + b"governed-result"


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    initial = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert initial.status_code == 200
    csrf = initial.json()["csrf_token"]
    changed = client.post(
        "/api/v1/auth/change-password",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json={"current_password": password, "new_password": f"new-{username}-correct-horse"},
    )
    assert changed.status_code == 200
    return {"Origin": ORIGIN, "X-CSRF-Token": changed.json()["csrf_token"]}


def _multipart_values(body: bytes, name: str) -> list[bytes]:
    marker = f'name="{name}"'.encode()
    values: list[bytes] = []
    for section in body.split(b"--"):
        if marker in section and b"\r\n\r\n" in section:
            values.append(section.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0])
    return values


def test_admin_cannot_create_a_chiyun_origin_or_send_provider_traffic(tmp_path: Path) -> None:
    store = CanvasStore(tmp_path / "data")
    provider_requests: list[httpx.Request] = []

    def provider(request: httpx.Request) -> httpx.Response:
        provider_requests.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(OUTPUT).decode()}]})

    factory = AdapterFactory(
        data_dir=store.data_dir,
        credential_resolver=MappingCredentialResolver({"chiyun-primary": "test-only-secret"}),
        asset_loader=_local_asset_loader(store.data_dir),
        transport=httpx.MockTransport(provider),
    )
    app = create_app(
        Settings("test", 8996, store.data_dir, "unused", identity_mode="local", allowed_origins=(ORIGIN,)),
        static_dir=tmp_path / "dist",
        canvas_store=store,
        adapter_factory=factory,
    )
    accounts = app.state.local_auth.bootstrap_accounts(())
    assert accounts.user is not None
    admin = TestClient(app, base_url=ORIGIN)
    user = TestClient(app, base_url=ORIGIN)
    admin_headers = _login(admin, accounts.admin_username, accounts.admin_password)
    user_headers = _login(user, accounts.user_username, accounts.user_password)

    created_provider = admin.post(
        "/api/v1/admin/model-registry/providers",
        headers=admin_headers,
        json={
            "provider_id": "chiyun",
            "display_name": "Chiyun",
            "adapter_type": "chiyun_openai_images",
            "base_url": "https://chiyun.example",
            "credential_ref": "chiyun-primary",
            "enabled": True,
        },
    )
    assert created_provider.status_code == 405
    assert app.state.canvas_store.provider_definition("chiyun") is None
    assert provider_requests == []
    assert user.get("/api/v1/models", headers=user_headers).json()["models"] == []

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


def test_admin_created_chiyun_model_runs_once_and_remains_owner_isolated(tmp_path: Path) -> None:
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
    assert created_provider.status_code == 201
    created_model = admin.post(
        "/api/v1/admin/model-registry/models",
        headers=admin_headers,
        json={
            "model_id": "chiyun-gpt-image-2",
            "provider_id": "chiyun",
            "provider_model_name": "gpt-image-2",
            "display_name": "GPT Image 2",
            "introduction": "多参考图编辑",
            "template_id": "chiyun_gpt_image_edit_v1",
            "enabled": True,
        },
    )
    assert created_model.status_code == 201
    granted = admin.put(
        f"/api/v1/admin/users/{accounts.user.user_id}/models",
        headers=admin_headers,
        json={"model_ids": ["chiyun-gpt-image-2"]},
    )
    assert granted.status_code == 200

    uploaded = user.post(
        "/api/v1/assets",
        headers=user_headers,
        files={"file": ("reference.png", PNG, "image/png")},
        data={"kind": "reference", "media_type": "image"},
    )
    assert uploaded.status_code == 201, uploaded.text
    asset_id = uploaded.json()["asset_id"]
    assert admin.get(f"/api/v1/assets/{asset_id}").status_code == 403

    models = user.get("/api/v1/models")
    assert models.status_code == 200
    assert [(item["model_id"], item["operations"]) for item in models.json()["models"]] == [
        ("chiyun-gpt-image-2", ["image.edit"])
    ]
    payload = {
        "operation": "image.edit",
        "model_id": "chiyun-gpt-image-2",
        "prompt": "保留 @图片1 的主体",
        "params": {"size": "1024x1024", "output_count": 1},
        "asset_ids": [],
        "inputs": {"reference_images": [asset_id]},
        "idempotency_key": "integration-chiyun-once",
    }
    async def submit_concurrently() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, cookies=user.cookies) as first_client, httpx.AsyncClient(
            transport=transport, base_url=ORIGIN, cookies=user.cookies
        ) as second_client:
            first, second = await asyncio.gather(
                first_client.post("/api/v1/jobs", headers=user_headers, json=payload),
                second_client.post("/api/v1/jobs", headers=user_headers, json=payload),
            )
            return first, second

    created, repeated = asyncio.run(submit_concurrently())
    assert created.status_code == 201 and repeated.status_code == 201
    assert created.json()["id"] == repeated.json()["id"]
    assert len(provider_requests) == 1
    request = provider_requests[0]
    assert request.method == "POST" and request.url.path == "/v1/images/edits"
    assert request.headers["authorization"] == "Bearer test-only-secret"
    assert _multipart_values(request.content, "model") == [b"gpt-image-2"]
    assert _multipart_values(request.content, "image[]") == [PNG]

    job_id = created.json()["id"]
    assert admin.get(f"/api/v1/jobs/{job_id}").status_code == 404
    completed = user.get(f"/api/v1/jobs/{job_id}")
    assert completed.status_code == 200 and completed.json()["status"] == "succeeded"
    assert admin.get(f"/api/v1/results/{job_id}").status_code == 404
    head = user.head(f"/api/v1/results/{job_id}")
    ranged = user.get(f"/api/v1/results/{job_id}", headers={"Range": "bytes=0-7"})
    full = user.get(f"/api/v1/results/{job_id}")
    assert head.status_code == 200 and head.headers["content-type"].startswith("image/png")
    assert ranged.status_code == 206 and ranged.content == OUTPUT[:8]
    assert full.status_code == 200 and full.content == OUTPUT

    revoked = admin.put(
        f"/api/v1/admin/users/{accounts.user.user_id}/models",
        headers=admin_headers,
        json={"model_ids": []},
    )
    assert revoked.status_code == 200
    rejected = user.post(
        "/api/v1/jobs",
        headers=user_headers,
        json={**payload, "idempotency_key": "integration-after-revoke"},
    )
    assert rejected.status_code == 400
    assert len(provider_requests) == 1

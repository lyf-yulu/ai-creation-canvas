from __future__ import annotations

import base64
from dataclasses import replace
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.factory import AdapterFactory, MappingCredentialResolver
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.storage.sqlite import CanvasStore
from tests.server.test_model_registry import _model, _provider


ORIGIN = "http://127.0.0.1:8996"
PNG = b"\x89PNG\r\n\x1a\n" + b"owned-reference"


def _login(client: TestClient, username: str, password: str) -> dict[str, object]:
    login = client.post("/api/v1/auth/login", json={"username": username, "password": password}).json()
    changed = client.post("/api/v1/auth/change-password", headers={"Origin": ORIGIN, "X-CSRF-Token": login["csrf_token"]}, json={"current_password": password, "new_password": f"new-{username}-correct-horse"})
    return changed.json()


def test_dynamic_model_job_persists_an_immutable_governed_snapshot(tmp_path: Path) -> None:
    store = CanvasStore(tmp_path / "data")
    store.create_provider_definition(_provider(), actor_user_id="bootstrap")
    store.create_model_definition(_model(), actor_user_id="bootstrap")
    asset_path = store.assets_dir / "reference.png"
    asset_path.write_bytes(PNG)
    seen: list[httpx.Request] = []
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
    factory = AdapterFactory(data_dir=store.data_dir, credential_resolver=MappingCredentialResolver({"chiyun-primary": "test-only-secret"}), asset_loader=lambda _: (PNG, "image/png"), transport=httpx.MockTransport(handler))
    app = create_app(Settings("test", 8996, store.data_dir, "unused", identity_mode="local", allowed_origins=(ORIGIN,)), static_dir=tmp_path / "dist", canvas_store=store, adapter_factory=factory)
    accounts = app.state.local_auth.bootstrap_accounts(())
    assert accounts.user is not None
    store.create_asset(asset_id="reference-1", user_id=accounts.user.user_id, kind="reference", mime_type="image/png", relative_path="assets/reference.png", size_bytes=len(PNG))
    store.grant_model_access(accounts.user.user_id, "chiyun-gpt-image-2", actor_user_id=accounts.admin.user_id)
    client = TestClient(app, base_url=ORIGIN)
    session = _login(client, accounts.user_username, accounts.user_password)
    headers = {"Origin": ORIGIN, "X-CSRF-Token": session["csrf_token"]}
    payload = {"operation": "image.edit", "model_id": "chiyun-gpt-image-2", "prompt": "keep @图片1", "params": {"size": "1024x1024", "output_count": 1}, "asset_ids": [], "inputs": {"reference_images": ["reference-1"]}, "idempotency_key": "governed-same-key"}

    created = client.post("/api/v1/jobs", headers=headers, json=payload)
    repeated = client.post("/api/v1/jobs", headers=headers, json=payload)
    assert created.status_code == 201 and repeated.status_code == 201
    assert created.json()["id"] == repeated.json()["id"]
    assert len(seen) == 1
    item, forbidden = store.job_for_owner(created.json()["id"], accounts.user.user_id)
    assert not forbidden and item is not None
    snapshot = json.loads(str(item["submission_json"]))
    assert snapshot == {"inputs": {"reference_images": ["reference-1"]}, "model_id": "chiyun-gpt-image-2", "operation": "image.edit", "params": {"output_count": 1, "size": "1024x1024"}, "prompt": "keep @图片1"}
    assert item["model_revision"] == 1
    assert item["provider_id"] == "chiyun"
    assert item["adapter_type"] == "chiyun_openai_images"
    changed_model = store.update_model_definition(replace(_model(), display_name="Changed later"), expected_revision=1, actor_user_id=accounts.admin.user_id)
    assert changed_model.revision == 2
    unchanged, _ = store.job_for_owner(created.json()["id"], accounts.user.user_id)
    assert unchanged is not None and unchanged["model_revision"] == 1
    assert json.loads(str(unchanged["submission_json"])) == snapshot

    conflict = client.post("/api/v1/jobs", headers=headers, json={**payload, "prompt": "changed"})
    assert conflict.status_code == 409
    assert len(seen) == 1

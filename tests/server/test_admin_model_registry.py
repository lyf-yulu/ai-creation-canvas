from __future__ import annotations

import os

from fastapi.testclient import TestClient

from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings


ORIGIN = "http://127.0.0.1:8996"


def _clients(tmp_path, monkeypatch):
    monkeypatch.setenv("AICC_CREDENTIAL_CHIYUN_PRIMARY", "test-only-secret")
    app = create_app(Settings("test", 8996, tmp_path / "data", "unused", identity_mode="local", allowed_origins=(ORIGIN,)), static_dir=tmp_path / "dist")
    accounts = app.state.local_auth.bootstrap_accounts(())
    admin, user = TestClient(app, base_url=ORIGIN), TestClient(app, base_url=ORIGIN)
    def login(client, username, password):
        initial = client.post("/api/v1/auth/login", json={"username": username, "password": password}).json()
        changed = client.post("/api/v1/auth/change-password", headers={"Origin": ORIGIN, "X-CSRF-Token": initial["csrf_token"]}, json={"current_password": password, "new_password": f"new-{username}-correct-horse"}).json()
        return {"Origin": ORIGIN, "X-CSRF-Token": changed["csrf_token"]}
    return app, accounts, admin, user, login(admin, accounts.admin_username, accounts.admin_password), login(user, accounts.user_username, accounts.user_password)


def test_admin_creates_governed_chiyun_model_and_grants_then_revokes_access(tmp_path, monkeypatch) -> None:
    app, accounts, admin, user, admin_headers, user_headers = _clients(tmp_path, monkeypatch)
    del user_headers
    assert accounts.user is not None
    provider = admin.post("/api/v1/admin/model-registry/providers", headers=admin_headers, json={
        "provider_id": "chiyun", "display_name": "Chiyun", "adapter_type": "chiyun_openai_images",
        "base_url": "https://chiyun.example", "credential_ref": "chiyun-primary", "enabled": True,
    })
    assert provider.status_code == 201
    assert provider.json()["base_url"] == "https://chiyun.example"
    assert provider.json()["credential_ref"] == "chiyun-primary"
    assert "api_key" not in provider.text.lower()

    model = admin.post("/api/v1/admin/model-registry/models", headers=admin_headers, json={
        "model_id": "chiyun-gpt-image-2", "provider_id": "chiyun", "provider_model_name": "gpt-image-2",
        "display_name": "GPT Image 2", "introduction": "多参考图编辑", "template_id": "chiyun_gpt_image_edit_v1", "enabled": True,
    })
    assert model.status_code == 201
    assert model.json()["modality"] == "image"
    assert model.json()["operations"] == ["image.edit"]
    assert "provider_model_name" not in model.text
    assert "parameter_mappings" not in model.text

    registry = admin.get("/api/v1/admin/model-registry")
    assert registry.status_code == 200
    assert registry.json()["providers"][0]["credential_available"] is True
    assert registry.json()["templates"][0]["template_id"] == "chiyun_gpt_image_edit_v1"

    granted = admin.put(f"/api/v1/admin/users/{accounts.user.user_id}/models", headers=admin_headers, json={"model_ids": ["chiyun-gpt-image-2"]})
    assert granted.status_code == 200
    assert [item["model_id"] for item in user.get("/api/v1/models").json()["models"]] == ["chiyun-gpt-image-2"]
    revoked = admin.put(f"/api/v1/admin/users/{accounts.user.user_id}/models", headers=admin_headers, json={"model_ids": []})
    assert revoked.status_code == 200
    assert user.get("/api/v1/models").json()["models"] == []
    assert [event["action"] for event in app.state.canvas_store.admin_audit_events()][-2:] == ["model_access.grant", "model_access.revoke"]


def test_registry_admin_endpoints_are_hidden_and_reject_freeform_protocols(tmp_path, monkeypatch) -> None:
    app, accounts, admin, user, admin_headers, user_headers = _clients(tmp_path, monkeypatch)
    del app, accounts
    assert user.get("/api/v1/admin/model-registry").status_code == 404
    assert user.post("/api/v1/admin/model-registry/providers", headers=user_headers, json={}).status_code == 404
    unknown_adapter = admin.post("/api/v1/admin/model-registry/providers", headers=admin_headers, json={
        "provider_id": "unsafe", "display_name": "Unsafe", "adapter_type": "python.module",
        "base_url": "https://unsafe.example", "credential_ref": "chiyun-primary", "enabled": True,
    })
    assert unknown_adapter.status_code == 400
    freeform = admin.post("/api/v1/admin/model-registry/models", headers=admin_headers, json={
        "model_id": "unsafe", "provider_id": "unsafe", "provider_model_name": "unsafe",
        "display_name": "Unsafe", "introduction": "Unsafe", "template_id": "custom", "enabled": True,
        "request_script": "import os",
    })
    assert freeform.status_code == 400

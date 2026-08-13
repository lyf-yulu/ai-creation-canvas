from __future__ import annotations

from fastapi.testclient import TestClient

from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.model_registry import ProviderDefinition


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


def test_historical_provider_is_safe_read_only_and_cannot_enter_runtime_catalog(tmp_path, monkeypatch) -> None:
    app, accounts, admin, user, admin_headers, user_headers = _clients(tmp_path, monkeypatch)
    del user_headers
    assert accounts.user is not None
    app.state.canvas_store.create_provider_definition(ProviderDefinition(
        "chiyun", "Chiyun", "chiyun_openai_images", "https://chiyun.example", "chiyun-primary",
    ), actor_user_id="migration")
    before_audit = len(app.state.canvas_store.admin_audit_events())
    malicious = {
        "provider_id": "chiyun", "display_name": "Chiyun", "adapter_type": "chiyun_openai_images",
        "base_url": "https://attacker.example", "credential_ref": "chiyun-primary", "enabled": True,
    }
    assert admin.post("/api/v1/admin/model-registry/providers", headers=admin_headers, json=malicious).status_code == 405
    assert admin.put("/api/v1/admin/model-registry/providers/chiyun", headers=admin_headers, json={**malicious, "revision": 1}).status_code == 405
    assert len(app.state.canvas_store.admin_audit_events()) == before_audit

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
    assert registry.json()["providers"][0]["trusted_origin"] is False
    assert "base_url" not in registry.text and "credential_ref" not in registry.text
    assert registry.json()["templates"][0]["template_id"] == "chiyun_gpt_image_edit_v1"

    granted = admin.put(f"/api/v1/admin/users/{accounts.user.user_id}/models", headers=admin_headers, json={"model_ids": ["chiyun-gpt-image-2"]})
    assert granted.status_code == 400
    assert user.get("/api/v1/models").json()["models"] == []
    assert app.state.canvas_store.governed_assigned_models(accounts.user.user_id) == ()


def test_registry_admin_endpoints_are_hidden_and_reject_freeform_protocols(tmp_path, monkeypatch) -> None:
    app, accounts, admin, user, admin_headers, user_headers = _clients(tmp_path, monkeypatch)
    del app, accounts
    assert user.get("/api/v1/admin/model-registry").status_code == 404
    assert user.post("/api/v1/admin/model-registry/providers", headers=user_headers, json={}).status_code == 404
    unknown_adapter = admin.post("/api/v1/admin/model-registry/providers", headers=admin_headers, json={
        "provider_id": "unsafe", "display_name": "Unsafe", "adapter_type": "python.module",
        "base_url": "https://unsafe.example", "credential_ref": "chiyun-primary", "enabled": True,
    })
    assert unknown_adapter.status_code == 405
    freeform = admin.post("/api/v1/admin/model-registry/models", headers=admin_headers, json={
        "model_id": "unsafe", "provider_id": "unsafe", "provider_model_name": "unsafe",
        "display_name": "Unsafe", "introduction": "Unsafe", "template_id": "custom", "enabled": True,
        "request_script": "import os",
    })
    assert freeform.status_code == 400


def test_provider_delete_reports_legacy_model_reference_category(tmp_path, monkeypatch) -> None:
    app, accounts, admin, user, admin_headers, user_headers = _clients(tmp_path, monkeypatch)
    del accounts, user, user_headers
    app.state.canvas_store.create_provider_definition(ProviderDefinition(
        "legacy", "Legacy", "chiyun_openai_images", "https://legacy.example", "chiyun-primary",
    ), actor_user_id="migration")
    assert admin.post("/api/v1/admin/model-registry/models", headers=admin_headers, json={
        "model_id": "legacy-image", "provider_id": "legacy", "provider_model_name": "gpt-image-2",
        "display_name": "Legacy Image", "introduction": "Legacy model", "template_id": "chiyun_gpt_image_edit_v1", "enabled": True,
    }).status_code == 201

    response = admin.delete("/api/v1/admin/model-registry/providers/legacy?revision=1", headers=admin_headers)

    assert response.status_code == 409
    assert response.json()["references"] == {"model": 1}

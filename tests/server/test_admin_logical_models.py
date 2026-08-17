from __future__ import annotations

import json

from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.factory import ProviderProtocol, RouteAdapterFactory
from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.catalog import ManagedRoutingRuntime
from ai_creation_canvas.config import Settings
from ai_creation_canvas.coordination import LocalExecutionCoordinator
from ai_creation_canvas.credential_pools import CredentialKey, CredentialPool
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.model_registry import ProviderDefinition
from ai_creation_canvas.routing import RouteSelector
from ai_creation_canvas.storage.sqlite import CanvasStore
from tests.server.test_model_assignments import AssignmentAdapter


ORIGIN = "http://127.0.0.1:45996"


def image_contract() -> dict[str, object]:
    from ai_creation_canvas.trusted_routing import trusted_route_presets
    return trusted_route_presets()[("banana", "chiyun")].operation_contracts[0].to_dict()


def video_contract() -> dict[str, object]:
    from ai_creation_canvas.trusted_routing import trusted_route_presets
    return trusted_route_presets()[("seedance", "ark")].operation_contracts[0].to_dict()


def model_body(model_id: str = "banana", *, modality: str = "image", contract: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "model_id": model_id,
        "display_name": "Banana" if modality == "image" else "Seedance",
        "introduction": "A managed logical model.",
        "modality": modality,
        "operation_contracts": [contract or (image_contract() if modality == "image" else video_contract())],
        "enabled": True,
    }


def route_body(route_id: str = "banana-t8", *, model_id: str = "banana", video: bool = False) -> dict[str, object]:
    return {
        "route_id": route_id,
        "model_id": model_id,
        "provider_id": "ark" if video else "chiyun-banana",
        "provider_model_name": "doubao-seedance-2-5-260628" if video else "banana2-ssvip",
        "adapter_type": "ark" if video else "chiyun_gemini_images",
        "credential_pool_ref": "seedance-official" if video else "t8-gemini",
        "family": "seedance" if video else "nano-banana",
        "operation_contracts": [video_contract() if video else image_contract()],
        "priority": 20,
        "max_concurrency": 4,
        "enabled": True,
    }


def _pool(pool_id: str, provider: str, group: str, family: str, key_id: str, secret: str, capacity: int = 2) -> CredentialPool:
    return CredentialPool(pool_id, provider, group, (family,), (CredentialKey(key_id, secret, capacity),), (pool_id[0] * 64))


def clients(tmp_path):
    store = CanvasStore(tmp_path / "data")
    store.create_provider_definition(ProviderDefinition("chiyun-banana", "Chiyun Banana", "chiyun_gemini_images", "https://chiyun.work", "unused"), actor_user_id="bootstrap")
    store.create_provider_definition(ProviderDefinition("t8star", "T8", "chiyun_openai_images", "https://t8.example", "unused"), actor_user_id="bootstrap")
    store.create_provider_definition(ProviderDefinition("ark", "Ark", "ark", "https://ark.cn-beijing.volces.com", "unused"), actor_user_id="bootstrap")
    pools = {
        "t8-gemini": _pool("t8-gemini", "chiyun-banana", "banana", "nano-banana", "gemini-key-1", "gemini-test-secret"),
        "t8-cc": _pool("t8-cc", "t8star", "cc", "claude", "cc-key-1", "cc-test-secret"),
        "seedance-official": _pool("seedance-official", "ark", "official", "seedance", "ark-key-1", "ark-test-secret", 3),
    }
    coordinator = LocalExecutionCoordinator(global_limit=8, provider_limit=8, user_limit=4)
    factory = RouteAdapterFactory(
        data_dir=tmp_path / "data",
        asset_loader=lambda asset_id: (_ for _ in ()).throw(KeyError(asset_id)),
        provider_protocols={
            "chiyun-banana": ProviderProtocol("chiyun-banana", "chiyun_gemini_images", "https://chiyun.work"),
            "t8star": ProviderProtocol.from_readonly_deployment("t8star", "chiyun_openai_images", "https://t8.example", approved_origin="https://t8.example"),
            "ark": ProviderProtocol("ark", "ark", "https://ark.cn-beijing.volces.com"),
        },
    )
    runtime = ManagedRoutingRuntime(store, lambda: pools, RouteSelector(), coordinator, factory)
    registry = AdapterRegistry()
    registry.register_generation(AssignmentAdapter())
    app = create_app(
        Settings("test", 45996, tmp_path / "data", "unused", identity_mode="local", allowed_origins=(ORIGIN,)),
        static_dir=tmp_path / "dist",
        canvas_store=store,
        registry=registry,
        model_catalog=ModelCatalog(registry),
        managed_routing_runtime=runtime,
    )
    accounts = app.state.local_auth.bootstrap_accounts(())
    admin, user = TestClient(app, base_url=ORIGIN), TestClient(app, base_url=ORIGIN)

    def login(client: TestClient, username: str, password: str) -> dict[str, str]:
        first = client.post("/api/v1/auth/login", json={"username": username, "password": password}).json()
        changed = client.post(
            "/api/v1/auth/change-password",
            headers={"Origin": ORIGIN, "X-CSRF-Token": first["csrf_token"]},
            json={"current_password": password, "new_password": f"new-{username}-correct-horse"},
        ).json()
        return {"Origin": ORIGIN, "X-CSRF-Token": changed["csrf_token"]}

    return app, accounts, admin, user, login(admin, accounts.admin_username, accounts.admin_password), login(user, accounts.user_username, accounts.user_password), pools


def test_pool_listing_without_managed_routing_is_empty(tmp_path) -> None:
    store = CanvasStore(tmp_path / "data")
    registry = AdapterRegistry()
    registry.register_generation(AssignmentAdapter())
    app = create_app(
        Settings("test", 45996, tmp_path / "data", "unused", identity_mode="local", allowed_origins=(ORIGIN,)),
        static_dir=tmp_path / "dist",
        canvas_store=store,
        registry=registry,
        model_catalog=ModelCatalog(registry),
    )
    accounts = app.state.local_auth.bootstrap_accounts(())
    admin = TestClient(app, base_url=ORIGIN)
    first = admin.post("/api/v1/auth/login", json={"username": accounts.admin_username, "password": accounts.admin_password}).json()
    changed = admin.post(
        "/api/v1/auth/change-password",
        headers={"Origin": ORIGIN, "X-CSRF-Token": first["csrf_token"]},
        json={"current_password": accounts.admin_password, "new_password": "new-admin-correct-horse"},
    ).json()
    headers = {"Origin": ORIGIN, "X-CSRF-Token": changed["csrf_token"]}
    response = admin.get("/api/v1/admin/credential-pools", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"pools": []}


def test_admin_logical_model_round_trip_for_image_and_video(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers, pools = clients(tmp_path)
    del app, accounts, user, user_headers, pools
    for body in (model_body(), model_body("seedance", modality="video")):
        created = admin.post("/api/v1/admin/logical-models", headers=admin_headers, json=body)
        assert created.status_code == 201
        assert created.json()["model_id"] == body["model_id"]
        assert created.json()["revision"] == 1

    listed = admin.get("/api/v1/admin/logical-models")
    assert [item["model_id"] for item in listed.json()["models"]] == ["banana", "seedance"]
    assert admin.get("/api/v1/admin/logical-models/banana").json()["operation_contracts"][0]["parameter_mappings"] == {
        "aspect_ratio": "aspectRatio", "image_size": "imageSize",
    }

    update = model_body()
    update.update({"display_name": "Banana Updated", "revision": 1})
    updated = admin.put("/api/v1/admin/logical-models/banana", headers=admin_headers, json=update)
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "Banana Updated"
    assert updated.json()["revision"] == 2

    stale = admin.put("/api/v1/admin/logical-models/banana", headers=admin_headers, json=update)
    assert stale.status_code == 409
    assert admin.get("/api/v1/admin/logical-models/banana").json()["display_name"] == "Banana Updated"


def test_all_logical_admin_routes_hide_before_body_validation(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers, pools = clients(tmp_path)
    del app, accounts, admin, admin_headers, pools
    calls = (
        ("get", "/api/v1/admin/logical-models", None),
        ("post", "/api/v1/admin/logical-models", {}),
        ("get", "/api/v1/admin/logical-models/missing", None),
        ("put", "/api/v1/admin/logical-models/missing", {}),
        ("delete", "/api/v1/admin/logical-models/missing?revision=no", None),
        ("post", "/api/v1/admin/logical-models/missing/disable", {}),
        ("post", "/api/v1/admin/logical-models/missing/archive", {}),
        ("post", "/api/v1/admin/logical-models/missing/restore", {}),
        ("post", "/api/v1/admin/logical-models/missing/purge-runtime", {}),
        ("get", "/api/v1/admin/logical-models/missing/routes", None),
        ("post", "/api/v1/admin/logical-models/missing/routes", {}),
        ("get", "/api/v1/admin/logical-models/missing/routes/missing", None),
        ("put", "/api/v1/admin/logical-models/missing/routes/missing", {}),
        ("delete", "/api/v1/admin/logical-models/missing/routes/missing?revision=no", None),
        ("post", "/api/v1/admin/logical-models/missing/routes/missing/disable", {}),
        ("post", "/api/v1/admin/logical-models/missing/routes/missing/archive", {}),
        ("post", "/api/v1/admin/logical-models/missing/routes/missing/restore", {}),
        ("post", "/api/v1/admin/logical-models/missing/routes/missing/purge-runtime", {}),
        ("get", "/api/v1/admin/credential-pools", None),
    )
    for method, path, body in calls:
        response = getattr(user, method)(path, headers=user_headers, json=body) if body is not None else getattr(user, method)(path, headers=user_headers)
        assert response.status_code == 404, (method, path, response.text)
        assert response.json()["code"] == "API_NOT_FOUND"


def test_pool_summaries_are_safe_and_use_live_local_capacity(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers, pools = clients(tmp_path)
    del accounts, user, user_headers
    response = admin.get("/api/v1/admin/credential-pools")
    assert response.status_code == 200
    summaries = response.json()["pools"]
    assert {item["pool_id"] for item in summaries} == set(pools)
    gemini = next(item for item in summaries if item["pool_id"] == "t8-gemini")
    seedance = next(item for item in summaries if item["pool_id"] == "seedance-official")
    assert gemini["adapter_type"] == "chiyun_gemini_images"
    assert seedance["adapter_type"] == "ark"
    assert gemini["total_capacity"] == 2
    assert gemini["available_count"] == 2
    assert gemini["busy_count"] == 0
    assert gemini["capacity_status"] == "available"
    assert gemini["circuit_status"] == "unsupported"
    assert gemini["circuit_open_count"] is None
    encoded = json.dumps(summaries)
    for pool in pools.values():
        for key in pool.keys:
            assert key.secret not in encoded
            assert key.key_id not in encoded
    assert "fingerprint" not in encoded.lower()


def test_logical_models_use_existing_assignment_api_without_exposing_routes(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers, pools = clients(tmp_path)
    del app, user_headers, pools
    assert accounts.user is not None
    assert admin.post("/api/v1/admin/logical-models", headers=admin_headers, json=model_body()).status_code == 201
    assert admin.post("/api/v1/admin/logical-models/banana/routes", headers=admin_headers, json=route_body()).status_code == 201

    granted = admin.put(
        f"/api/v1/admin/users/{accounts.user.user_id}/models",
        headers=admin_headers,
        json={"model_ids": ["banana"]},
    )

    assert granted.status_code == 200
    public = user.get("/api/v1/models")
    assert public.status_code == 200
    assert [item["model_id"] for item in public.json()["models"]] == ["banana"]
    lowered = public.text.lower()
    for forbidden in ("route_id", "provider_id", "credential", "pool", "group", "key"):
        assert forbidden not in lowered


def test_stale_logical_model_revision_is_409_before_contract_compatibility(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers, pools = clients(tmp_path)
    del accounts, user, user_headers, pools
    admin.post("/api/v1/admin/logical-models", headers=admin_headers, json=model_body())
    update = model_body()
    update.update({"revision": 1, "display_name": "revision-two"})
    assert admin.put("/api/v1/admin/logical-models/banana", headers=admin_headers, json=update).status_code == 200
    stale = model_body()
    stale.update({"revision": 1, "operation_contracts": [video_contract()], "modality": "video"})

    response = admin.put("/api/v1/admin/logical-models/banana", headers=admin_headers, json=stale)

    assert response.status_code == 409
    assert response.json()["code"] == "REVISION_CONFLICT"


def test_assignment_api_atomically_replaces_mixed_static_and_logical_models_with_audit(tmp_path, monkeypatch) -> None:
    app, accounts, admin, user, admin_headers, user_headers, pools = clients(tmp_path)
    del user_headers, pools
    assert accounts.user is not None
    admin.post("/api/v1/admin/logical-models", headers=admin_headers, json=model_body())
    admin.post("/api/v1/admin/logical-models/banana/routes", headers=admin_headers, json=route_body())
    monkeypatch.setattr(app.state.canvas_store, "replace_model_assignments", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy static path")))
    monkeypatch.setattr(app.state.canvas_store, "replace_governed_model_access", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy governed path")))

    response = admin.put(
        f"/api/v1/admin/users/{accounts.user.user_id}/models",
        headers=admin_headers,
        json={"model_ids": ["visible-model", "banana"]},
    )

    assert response.status_code == 200
    assert app.state.canvas_store.assigned_models(accounts.user.user_id) == ("visible-model",)
    assert app.state.canvas_store.governed_assigned_models(accounts.user.user_id) == ("banana",)
    assert {item["model_id"] for item in user.get("/api/v1/models").json()["models"]} == {"visible-model", "banana"}
    actions = [event["action"] for event in app.state.canvas_store.admin_audit_events()]
    assert actions[-2:] == ["model_assignment.grant", "model_access.grant"]

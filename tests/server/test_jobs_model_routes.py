from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.factory import ProviderProtocol, RouteAdapterFactory
from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.catalog import ManagedRoutingRuntime
from ai_creation_canvas.config import Settings
from ai_creation_canvas.coordination import CredentialLease, ExecutionCapacityExceeded
from ai_creation_canvas.credential_pools import CredentialKey, CredentialPool
from ai_creation_canvas.domain.models import ModelInputPort
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.model_registry import OperationContract
from ai_creation_canvas.model_routing import LogicalModelDefinition, ModelRouteDefinition
from ai_creation_canvas.routing import RouteSelector
from ai_creation_canvas.storage.sqlite import CanvasStore
from tests.server.test_model_registry import _provider


ORIGIN = "http://127.0.0.1:45991"
PNG = b"\x89PNG\r\n\x1a\nmanaged-result"


def headers(user: str = "user-a") -> dict[str, str]:
    timestamp = str(int(time.time()))
    payload = f"v2\n{timestamp}\n{user}\nuser\n{quote('Alice', safe='')}"
    signature = hmac.new(b"test-signing-secret", payload.encode(), hashlib.sha256).hexdigest()
    return {"X-Portal-Sig-Version": "2", "X-Portal-Timestamp": timestamp, "X-Portal-User-Id": user, "X-Portal-Username": "Alice", "X-Portal-Role": "user", "X-Portal-Signature": signature, "Cookie": "portal_session=test"}


def contract() -> OperationContract:
    return OperationContract(
        "image.edit",
        (ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 1, 10)),
        "image",
        {"type": "object", "properties": {"size": {"type": "string", "enum": ["auto", "1024x1024"], "default": "auto"}, "output_count": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1}}, "required": ["size", "output_count"], "additionalProperties": False},
        {"size": "size", "output_count": "n"},
    )


def logical() -> LogicalModelDefinition:
    return LogicalModelDefinition("nano-banana", "Nano Banana", "Logical model", "image", (contract(),), revision=1)


def route(route_id: str, provider: str, pool_id: str, priority: int) -> ModelRouteDefinition:
    return ModelRouteDefinition(route_id, "nano-banana", provider, "gemini-image", "chiyun_openai_images", pool_id, "nano-banana", (contract(),), priority, 1, revision=1)


def pool(pool_id: str, provider: str, group: str, keys: tuple[str, ...]) -> CredentialPool:
    return CredentialPool(pool_id, provider, group, ("nano-banana",), tuple(CredentialKey(key, f"secret-value-{key}", 1) for key in keys), hashlib.sha256(pool_id.encode()).hexdigest())


class ScriptedCoordinator:
    def __init__(self) -> None:
        self.acquisitions: list[tuple[str, str]] = []
        self.legacy_calls = 0

    def acquire(self, *args, **kwargs):
        self.legacy_calls += 1
        raise AssertionError("managed route touched legacy coordinator")

    @asynccontextmanager
    async def acquire_credential(self, job_id, user_id, candidate):
        if candidate.route.route_id == "official-route":
            raise ExecutionCapacityExceeded("busy")
        key = candidate.pool.keys[0]
        self.acquisitions.append((candidate.route.route_id, key.key_id))
        yield CredentialLease(candidate.route.route_id, candidate.pool.pool_id, key.key_id, key.secret, hashlib.sha256(key.secret.encode()).hexdigest(), "owner")

    def fingerprint_secret(self, secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()


def build_app(tmp_path: Path, handler, coordinator: ScriptedCoordinator):
    store = CanvasStore(tmp_path / "data")
    store.create_provider_definition(_provider(), actor_user_id="bootstrap")
    store.create_logical_model(logical())
    store.create_model_route(route("official-route", "google", "official", 1))
    store.create_model_route(route("gemini-route", "t8star", "gemini", 2))
    pools = {
        "official": pool("official", "google", "official", ("official-a",)),
        "gemini": pool("gemini", "t8star", "gemini", ("gemini-a", "gemini-b")),
        "cc": pool("cc", "t8star", "cc", ("cc-a",)),
    }
    factory = RouteAdapterFactory(
        data_dir=store.data_dir,
        asset_loader=lambda _: (PNG, "image/png"),
        provider_protocols={
            "google": ProviderProtocol("google", "chiyun_openai_images", "https://google.example"),
            "t8star": ProviderProtocol("t8star", "chiyun_openai_images", "https://t8.example"),
        },
        transport=httpx.MockTransport(handler),
    )
    runtime = ManagedRoutingRuntime(store, lambda: pools, RouteSelector(), coordinator, factory)
    registry = AdapterRegistry()
    app = create_app(Settings("test", 45991, store.data_dir, "test-signing-secret"), static_dir=tmp_path / "dist", registry=registry, model_catalog=ModelCatalog(registry), canvas_store=store, managed_routing_runtime=runtime)
    with store._connection(immediate=True) as db:
        db.execute("INSERT INTO canvas_model_access(user_id,model_id,granted_by,granted_at,revoked_at) VALUES ('user-a','nano-banana','bootstrap','2026-08-12T00:00:00+00:00',NULL)")
    store.create_asset(asset_id="reference-1", user_id="user-a", kind="reference", mime_type="image/png", relative_path="assets/reference.png", size_bytes=len(PNG), media_type="image")
    return app, store, pools


def payload(**changes):
    body = {"operation": "image.edit", "model_id": "nano-banana", "prompt": "edit @图片1", "params": {"size": "1024x1024", "output_count": 1}, "asset_ids": [], "inputs": {"reference_images": ["reference-1"]}, "idempotency_key": "managed-key"}
    body.update(changes)
    return body


def test_managed_route_retries_explicit_429_on_next_compatible_key_and_persists_safe_snapshot(tmp_path: Path) -> None:
    seen_auth: list[str] = []
    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers["authorization"]
        seen_auth.append(auth)
        if auth == "Bearer secret-value-gemini-a":
            return httpx.Response(429, json={"error": "busy"})
        assert auth == "Bearer secret-value-gemini-b"
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
    coordinator = ScriptedCoordinator()
    app, store, pools = build_app(tmp_path, handler, coordinator)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/v1/jobs", headers=headers(), json=payload())

    assert response.status_code == 201, response.text
    assert coordinator.legacy_calls == 0
    assert coordinator.acquisitions == [("gemini-route", "gemini-a"), ("gemini-route", "gemini-b")]
    assert all("cc" not in item for pair in coordinator.acquisitions for item in pair)
    item, forbidden = store.job_for_owner(response.json()["id"], "user-a")
    assert not forbidden and item is not None
    assert item["logical_model_id"] == "nano-banana" and item["logical_model_revision"] == 1
    assert item["route_id"] == "gemini-route" and item["route_revision"] == 1
    assert item["pool_revision_digest"] == pools["gemini"].revision_digest
    assert item["submission_state"] == "submitted"
    encoded = json.dumps(item, sort_keys=True)
    for forbidden_value in ("secret-value", "gemini-a", "gemini-b", "cc-a"):
        assert forbidden_value not in encoded
    original_snapshot = item["route_snapshot_json"]
    store.update_model_route(route("gemini-route", "t8star", "gemini", 99), expected_revision=1)
    changed, _ = store.job_for_owner(response.json()["id"], "user-a")
    assert changed is not None and changed["route_snapshot_json"] == original_snapshot
    completed = client.get(f"/api/v1/jobs/{response.json()['id']}", headers=headers())
    assert completed.status_code == 200 and completed.json()["status"] == "succeeded"
    pools.clear()
    downloaded = client.get(f"/api/v1/results/{response.json()['id']}", headers=headers())
    assert downloaded.status_code == 200
    assert downloaded.content == PNG


def test_managed_rejection_before_credentials_and_never_falls_back(tmp_path: Path) -> None:
    coordinator = ScriptedCoordinator()
    app, store, _ = build_app(tmp_path, lambda request: (_ for _ in ()).throw(AssertionError("provider called")), coordinator)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/v1/jobs", headers=headers(), json=payload(operation="video.generate"))

    assert response.status_code == 400
    assert coordinator.acquisitions == [] and coordinator.legacy_calls == 0

    store.revoke_model_access("user-a", "nano-banana", actor_user_id="admin")
    revoked = client.post("/api/v1/jobs", headers=headers(), json=payload(idempotency_key="revoked"))
    assert revoked.status_code == 400
    assert coordinator.acquisitions == [] and coordinator.legacy_calls == 0


def test_production_logical_routes_require_pool_configuration_after_redis(tmp_path: Path) -> None:
    store = CanvasStore(tmp_path / "data")
    store.create_logical_model(logical())
    store.create_model_route(route("official-route", "google", "official", 1))
    with pytest.raises(ValueError, match="credential pool"):
        create_app(
            Settings("production", 8991, store.data_dir, "deployment-secret", redis_url="redis://127.0.0.1:6379/0"),
            static_dir=tmp_path / "dist", canvas_store=store,
            registry=AdapterRegistry(), model_catalog=ModelCatalog(AdapterRegistry()),
            execution_coordinator=ScriptedCoordinator(),
        )


def test_production_startup_rejects_route_without_trusted_provider_protocol(tmp_path: Path, monkeypatch) -> None:
    store = CanvasStore(tmp_path / "data")
    store.create_provider_definition(_provider(), actor_user_id="bootstrap")
    store.create_logical_model(logical())
    store.create_model_route(route("official-route", "google", "official", 1))
    pools = tmp_path / "credential-pools.yaml"
    pools.write_text("version: 1\npools:\n  official:\n    provider: google\n    group: official\n    allowed_families: [nano-banana]\n    keys:\n      - id: official-a\n        api_key: deployment-secret-value\n        max_concurrency: 1\n", encoding="utf-8")
    pools.chmod(0o600)
    monkeypatch.setenv("AICC_CREDENTIAL_HMAC_KEY", "h" * 32)
    settings = Settings(
        "production", 8991, store.data_dir, "deployment-secret",
        redis_url="redis://127.0.0.1:6379/0",
        credential_pools_path=pools, credential_pools_root=tmp_path,
    )
    with pytest.raises(ValueError, match="provider protocol"):
        create_app(
            settings, static_dir=tmp_path / "dist", canvas_store=store,
            registry=AdapterRegistry(), model_catalog=ModelCatalog(AdapterRegistry()),
            execution_coordinator=ScriptedCoordinator(),
        )

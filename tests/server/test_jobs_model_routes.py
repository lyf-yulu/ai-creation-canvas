from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from dataclasses import replace
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
from ai_creation_canvas import catalog as catalog_module
from ai_creation_canvas.config import Settings
from ai_creation_canvas.coordination import CredentialLease, CoordinationUnavailable, ExecutionCapacityExceeded
from ai_creation_canvas.credential_pools import CredentialKey, CredentialPool
from ai_creation_canvas.domain.models import ModelInputPort
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.managed_jobs import validated_job_route
from ai_creation_canvas.model_registry import OperationContract
from ai_creation_canvas.model_registry import ProviderDefinition
from ai_creation_canvas.model_routing import LogicalModelDefinition, ModelRouteDefinition
from ai_creation_canvas.routing import RouteSelector
from ai_creation_canvas.storage.sqlite import CanvasStore
from ai_creation_canvas.trusted_routing import trusted_route_presets
from tests.server.test_model_registry import _provider


ORIGIN = "http://127.0.0.1:45991"
PNG = b"\x89PNG\r\n\x1a\nmanaged-result"


def test_provider_submission_budget_is_atomic_and_never_exceeds_twenty() -> None:
    budget_type = getattr(catalog_module, "ProviderSubmissionBudget", None)
    assert budget_type is not None
    budget = budget_type(20)

    def consume(_: int) -> bool:
        try:
            budget.consume()
        except Exception as error:
            assert error.__class__.__name__ == "ProviderSubmissionBudgetExhausted"
            return False
        return True

    with ThreadPoolExecutor(max_workers=32) as pool:
        results = tuple(pool.map(consume, range(128)))

    assert sum(results) == 20
    assert budget.used == 20
    assert budget.remaining == 0


def headers(user: str = "user-a") -> dict[str, str]:
    timestamp = str(int(time.time()))
    payload = f"v2\n{timestamp}\n{user}\nuser\n{quote('Alice', safe='')}"
    signature = hmac.new(b"test-signing-secret", payload.encode(), hashlib.sha256).hexdigest()
    return {"X-Portal-Sig-Version": "2", "X-Portal-Timestamp": timestamp, "X-Portal-User-Id": user, "X-Portal-Username": "Alice", "X-Portal-Role": "user", "X-Portal-Signature": signature, "Cookie": "portal_session=test"}


def contract() -> OperationContract:
    return trusted_route_presets()[("gpt_image2", "chiyun")].operation_contracts[0]


def logical() -> LogicalModelDefinition:
    return LogicalModelDefinition("nano-banana", "Nano Banana", "Logical model", "image", (contract(),), revision=1)


def route(route_id: str, pool_id: str, priority: int) -> ModelRouteDefinition:
    preset = trusted_route_presets()[("gpt_image2", "chiyun")]
    return ModelRouteDefinition(route_id, "nano-banana", preset.provider_id, preset.provider_model_name, preset.adapter_type, pool_id, preset.family, (contract(),), priority, 1, revision=1)


def pool(pool_id: str, group: str, keys: tuple[str, ...]) -> CredentialPool:
    return CredentialPool(pool_id, "chiyun-gpt-image2", group, ("gpt-image",), tuple(CredentialKey(key, f"secret-value-{key}", 1) for key in keys), hashlib.sha256(pool_id.encode()).hexdigest())


class ScriptedCoordinator:
    def __init__(self, *, busy_official: bool = True) -> None:
        self.acquisitions: list[tuple[str, str]] = []
        self.legacy_calls = 0
        self.busy_official = busy_official

    def acquire(self, *args, **kwargs):
        self.legacy_calls += 1
        raise AssertionError("managed route touched legacy coordinator")

    @asynccontextmanager
    async def acquire_credential(self, job_id, user_id, candidate):
        if self.busy_official and candidate.route.route_id == "official-route":
            raise ExecutionCapacityExceeded("busy")
        key = candidate.pool.keys[0]
        self.acquisitions.append((candidate.route.route_id, key.key_id))
        yield CredentialLease(candidate.route.route_id, candidate.pool.pool_id, key.key_id, key.secret, hashlib.sha256(key.secret.encode()).hexdigest(), "owner")

    def fingerprint_secret(self, secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()


def build_app(tmp_path: Path, handler, coordinator: ScriptedCoordinator, *, store: CanvasStore | None = None, submission_budget=None):
    store = store or CanvasStore(tmp_path / "data")
    store.create_provider_definition(ProviderDefinition("chiyun-gpt-image2", "Chiyun GPT Image 2", "chiyun_openai_images", "https://chiyun.work", "managed"), actor_user_id="bootstrap")
    store.create_logical_model(logical())
    store.create_model_route(route("official-route", "official", 1))
    store.create_model_route(route("gemini-route", "gemini", 2))
    pools = {
        "official": pool("official", "official", ("official-a",)),
        "gemini": pool("gemini", "gemini", ("gemini-a", "gemini-b")),
        "cc": pool("cc", "cc", ("cc-a",)),
    }
    factory = RouteAdapterFactory(
        data_dir=store.data_dir,
        asset_loader=lambda _: (PNG, "image/png"),
        provider_protocols={
            "chiyun-gpt-image2": ProviderProtocol("chiyun-gpt-image2", "chiyun_openai_images", "https://chiyun.work"),
        },
        transport=httpx.MockTransport(handler),
        trusted_route_validator=lambda _route: None,
    )
    runtime = ManagedRoutingRuntime(store, lambda: pools, RouteSelector(), coordinator, factory, submission_budget)
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


def test_provider_submission_budget_counts_every_route_and_key_attempt_before_io(tmp_path: Path) -> None:
    provider_posts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal provider_posts
        provider_posts += 1
        return httpx.Response(503, json={"error": "temporarily unavailable"})

    budget = catalog_module.ProviderSubmissionBudget(2)
    coordinator = ScriptedCoordinator(busy_official=False)
    app, store, _ = build_app(tmp_path, handler, coordinator, submission_budget=budget)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/v1/jobs", headers=headers(), json=payload())

    assert response.status_code == 503
    assert response.json()["code"] == "PAID_CALL_BUDGET_EXHAUSTED"
    assert provider_posts == 2
    assert budget.used == 2 and budget.remaining == 0
    assert coordinator.acquisitions[:2] == [("official-route", "official-a"), ("gemini-route", "gemini-a")]
    jobs = store.list_jobs_for_owner("user-a")
    assert len(jobs) == 1 and jobs[0]["status"] == "failed" and jobs[0]["error_code"] == "PAID_CALL_BUDGET_EXHAUSTED"

    replay = client.post("/api/v1/jobs", headers=headers(), json=payload())
    assert replay.status_code == 201 and replay.json()["status"] == "failed"
    assert provider_posts == 2 and budget.used == 2


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
    assert validated_job_route(item).route_id == "gemini-route"
    assert item["submission_state"] == "submitted"
    encoded = json.dumps(item, sort_keys=True)
    for forbidden_value in ("secret-value", "gemini-a", "gemini-b", "cc-a"):
        assert forbidden_value not in encoded
    original_snapshot = item["route_snapshot_json"]
    store.update_model_route(route("gemini-route", "gemini", 99), expected_revision=1)
    changed, _ = store.job_for_owner(response.json()["id"], "user-a")
    assert changed is not None and changed["route_snapshot_json"] == original_snapshot
    legacy_snapshot = json.loads(str(original_snapshot))
    legacy_snapshot.pop("pool_revision_digest", None)
    legacy_snapshot.pop("schema_version", None)
    legacy_encoded = json.dumps(legacy_snapshot, sort_keys=True, separators=(",", ":"))
    with store._connection(immediate=True) as db:
        db.execute("UPDATE canvas_jobs SET route_snapshot_json=? WHERE id=?", (legacy_encoded, response.json()["id"]))
    completed = client.get(f"/api/v1/jobs/{response.json()['id']}", headers=headers())
    assert completed.status_code == 200 and completed.json()["status"] == "succeeded"
    pools.clear()
    downloaded = client.get(f"/api/v1/results/{response.json()['id']}", headers=headers())
    assert downloaded.status_code == 200
    assert downloaded.content == PNG
    headed = client.head(f"/api/v1/results/{response.json()['id']}", headers=headers())
    ranged = client.get(f"/api/v1/results/{response.json()['id']}", headers={**headers(), "Range": "bytes=0-7"})
    assert headed.status_code == 200 and ranged.status_code == 206 and ranged.content == PNG[:8]


def test_managed_route_retries_an_explicit_5xx_but_never_a_business_4xx(tmp_path: Path) -> None:
    statuses = iter((503, 200))
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers["authorization"])
        status = next(statuses)
        if status == 503:
            return httpx.Response(status, json={"error": "temporary"})
        return httpx.Response(status, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    coordinator = ScriptedCoordinator()
    app, _, _ = build_app(tmp_path, handler, coordinator)
    response = TestClient(app, raise_server_exceptions=False).post("/api/v1/jobs", headers=headers(), json=payload())

    assert response.status_code == 201
    assert seen_auth == ["Bearer secret-value-gemini-a", "Bearer secret-value-gemini-b"]
    assert coordinator.acquisitions == [("gemini-route", "gemini-a"), ("gemini-route", "gemini-b")]


def test_unknown_future_snapshot_version_fails_closed(tmp_path: Path) -> None:
    app, store, _ = build_app(
        tmp_path,
        lambda request: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]}),
        ScriptedCoordinator(),
    )
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post("/api/v1/jobs", headers=headers(), json=payload())
    assert created.status_code == 201
    item, _ = store.job_for_owner(created.json()["id"], "user-a")
    snapshot = json.loads(str(item["route_snapshot_json"]))
    snapshot["schema_version"] = 999
    with store._connection(immediate=True) as db:
        db.execute("UPDATE canvas_jobs SET route_snapshot_json=? WHERE id=?", (json.dumps(snapshot, sort_keys=True, separators=(",", ":")), created.json()["id"]))
    polled = client.get(f"/api/v1/jobs/{created.json()['id']}", headers=headers())
    assert polled.status_code == 200 and polled.json()["status"] == "failed"


def test_provider_business_rejection_returns_422_once(tmp_path: Path) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, json={"error": "invalid request"})

    app, _, _ = build_app(tmp_path, handler, ScriptedCoordinator())
    client = TestClient(app, raise_server_exceptions=False)
    first = client.post("/api/v1/jobs", headers=headers(), json=payload())
    repeated = client.post("/api/v1/jobs", headers=headers(), json=payload())
    assert first.status_code == 422
    assert repeated.status_code == 201 and repeated.json()["status"] == "failed"
    assert attempts == 1


def test_legacy_v1_ark_snapshot_keeps_local_get_head_and_range_without_key(tmp_path: Path) -> None:
    app, store, pools = build_app(tmp_path, lambda request: (_ for _ in ()).throw(AssertionError("network called")), ScriptedCoordinator())
    result_id = "ark_result_" + "a" * 64
    root = store.data_dir / "ark-results"
    root.mkdir(mode=0o700, exist_ok=True)
    (root / result_id).write_bytes(PNG)
    (root / f"{result_id}.json").write_text(json.dumps({"mime": "image/png"}), encoding="utf-8")
    ark_contract = OperationContract(
        "image.generate", (ModelInputPort("prompt", "text", 1, 1),), "image",
        {"type": "object", "properties": {}, "additionalProperties": False}, {},
    )
    ark_route = ModelRouteDefinition("historical-ark", "nano-banana", "google", "ark-model", "ark", "official", "nano-banana", (ark_contract,), 1, 1, revision=1)
    snapshot = {
        "route_id": ark_route.route_id, "model_id": ark_route.model_id, "provider_id": ark_route.provider_id,
        "provider_model_name": ark_route.provider_model_name, "adapter_type": ark_route.adapter_type,
        "credential_pool_ref": ark_route.credential_pool_ref, "family": ark_route.family,
        "operation_contracts": [ark_contract.to_dict()], "priority": 1, "max_concurrency": 1,
        "enabled": True, "archived_at": None, "revision": 1,
    }
    reserved = store.reserve_job(user_id="user-a", job_id="legacy-ark-job", service_id="nano-banana", operation="image.generate", idempotency_key="legacy-ark", request_hash="f" * 64, logical_model_id="nano-banana", logical_model_revision=1)
    token = str(reserved.job["submission_token"])
    store.record_routing_snapshot(
        "legacy-ark-job", token, logical_model_id="nano-banana", logical_model_revision=1,
        route_id="historical-ark", route_revision=1, pool_revision_digest=pools["official"].revision_digest,
        key_fingerprint="b" * 64, route_snapshot_json=json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
    )
    store.mark_submitted("legacy-ark-job", "legacy-upstream", "succeeded", token, result_ids=(result_id,))
    pools.clear()
    client = TestClient(app, raise_server_exceptions=False)
    downloaded = client.get("/api/v1/results/legacy-ark-job", headers=headers())
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == PNG
    assert client.head("/api/v1/results/legacy-ark-job", headers=headers()).status_code == 200
    ranged = client.get("/api/v1/results/legacy-ark-job", headers={**headers(), "Range": "bytes=0-7"})
    assert ranged.status_code == 206 and ranged.content == PNG[:8]


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


def test_access_revoked_immediately_after_reservation_stops_before_credential(tmp_path: Path, monkeypatch) -> None:
    coordinator = ScriptedCoordinator()
    app, store, _ = build_app(tmp_path, lambda request: (_ for _ in ()).throw(AssertionError("provider called")), coordinator)
    original = store.reserve_job

    def reserve_then_revoke(**kwargs):
        result = original(**kwargs)
        store.revoke_model_access("user-a", "nano-banana", actor_user_id="admin")
        return result

    monkeypatch.setattr(store, "reserve_job", reserve_then_revoke)
    response = TestClient(app, raise_server_exceptions=False).post("/api/v1/jobs", headers=headers(), json=payload())
    assert response.status_code == 400
    assert coordinator.acquisitions == []


def test_coordination_outage_returns_503_not_capacity_429(tmp_path: Path) -> None:
    class UnavailableCoordinator(ScriptedCoordinator):
        @asynccontextmanager
        async def acquire_credential(self, job_id, user_id, candidate):
            raise CoordinationUnavailable("offline")
            yield

    app, _, _ = build_app(tmp_path, lambda request: (_ for _ in ()).throw(AssertionError("provider called")), UnavailableCoordinator())
    response = TestClient(app, raise_server_exceptions=False).post("/api/v1/jobs", headers=headers(), json=payload())
    assert response.status_code == 503


def test_provider_disable_hides_catalog_and_stops_submission(tmp_path: Path) -> None:
    coordinator = ScriptedCoordinator()
    app, store, _ = build_app(tmp_path, lambda request: (_ for _ in ()).throw(AssertionError("provider called")), coordinator)
    for provider in store.list_provider_definitions():
        store.update_provider_definition(replace(provider, enabled=False), expected_revision=provider.revision, actor_user_id="admin")
    client = TestClient(app, raise_server_exceptions=False)
    catalog = client.get("/api/v1/models", headers=headers())
    submitted = client.post("/api/v1/jobs", headers=headers(), json=payload())
    assert catalog.status_code == 200 and catalog.json()["models"] == []
    assert submitted.status_code == 400 and coordinator.acquisitions == []


def test_production_logical_routes_require_pool_configuration_after_redis(tmp_path: Path) -> None:
    store = CanvasStore(tmp_path / "data")
    store.create_logical_model(logical())
    store.create_model_route(route("official-route", "official", 1))
    with pytest.raises(ValueError, match="credential pool"):
        create_app(
            Settings("production", 8991, store.data_dir, "deployment-secret", redis_url="redis://127.0.0.1:6379/0"),
            static_dir=tmp_path / "dist", canvas_store=store,
            registry=AdapterRegistry(), model_catalog=ModelCatalog(AdapterRegistry()),
            execution_coordinator=ScriptedCoordinator(),
        )


def test_restart_keeps_historical_untrusted_provider_out_of_protocol_map(tmp_path: Path, monkeypatch) -> None:
    store = CanvasStore(tmp_path / "data")
    store.create_provider_definition(_provider(), actor_user_id="bootstrap")
    store.create_logical_model(logical())
    store.create_model_route(route("official-route", "official", 1))
    pools = tmp_path / "credential-pools.yaml"
    pools.write_text("version: 1\npools:\n  official:\n    provider: google\n    group: official\n    allowed_families: [nano-banana]\n    keys:\n      - id: official-a\n        api_key: deployment-secret-value\n        max_concurrency: 1\n", encoding="utf-8")
    pools.chmod(0o600)
    monkeypatch.setenv("AICC_CREDENTIAL_HMAC_KEY", "h" * 32)
    settings = Settings(
        "test", 8992, store.data_dir, "deployment-secret", identity_mode="local", allowed_origins=("http://127.0.0.1:8992",),
        credential_pools_path=pools, credential_pools_root=tmp_path,
    )
    app = create_app(
        settings, static_dir=tmp_path / "dist", canvas_store=store,
        registry=AdapterRegistry(), model_catalog=ModelCatalog(AdapterRegistry()),
    )
    assert repr(app.state.managed_routing_runtime.adapter_factory) == "RouteAdapterFactory(provider_count=0)"

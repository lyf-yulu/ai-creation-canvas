from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.ark import _local_asset_loader
from ai_creation_canvas.adapters.factory import ProviderProtocol, RouteAdapterFactory
from ai_creation_canvas.adapters.portal.catalog import ModelCatalog, PortalJobsAdapter, ServiceDeclaration
from ai_creation_canvas.adapters.portal.client import PortalClient
from ai_creation_canvas.app import create_app
from ai_creation_canvas.catalog import ManagedRoutingRuntime
from ai_creation_canvas.config import Settings
from ai_creation_canvas.coordination import LocalExecutionCoordinator
from ai_creation_canvas.credential_pools import CredentialKey, CredentialPool
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.model_registry import ProviderDefinition
from ai_creation_canvas.model_routing import LogicalModelDefinition, ModelRouteDefinition
from ai_creation_canvas.routing import RouteSelector
from ai_creation_canvas.storage.sqlite import CanvasStore
from ai_creation_canvas.trusted_routing import trusted_route_presets


ORIGIN = "http://127.0.0.1:46121"
SIGNING_SECRET = b"offline-restart-signing-secret"
DIRECT_RESULT = b"\x89PNG\r\n\x1a\ndirect-recovered-result"
MANAGED_RESULTS = (
    b"\x89PNG\r\n\x1a\nmanaged-recovered-result-a",
    b"\x89PNG\r\n\x1a\nmanaged-recovered-result-b",
)
REFERENCE = b"\x89PNG\r\n\x1a\nrestart-reference"
ORIGINAL_MANAGED_SECRET = "offline-original-managed-secret"


class OneChunkStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def __aiter__(self):
        yield self.content


class MutableClock:
    def __init__(self) -> None:
        self.value = 1_800_000_000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float = 60.0) -> None:
        self.value += seconds


class DirectProviderTransport:
    """Stateful Portal-shaped provider with no socket or external state."""

    def __init__(self) -> None:
        self.ready = False
        self.catalog_gets = 0
        self.submits = 0
        self.polls = 0
        self.result_reads = 0

    def advance(self) -> None:
        self.ready = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/direct/api/config":
            self.catalog_gets += 1
            return httpx.Response(200, json={"models": [{
                "id": "direct-image",
                "display_name": "Direct recovery fixture",
                "operations": ["image.generate"],
                "input_media": ["text"],
                "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            }]})
        if request.method == "POST" and path == "/direct/api/jobs":
            self.submits += 1
            return httpx.Response(201, json={"id": "direct-upstream", "status": "queued"})
        if request.method == "GET" and path == "/direct/api/jobs/direct-upstream":
            self.polls += 1
            if self.ready:
                return httpx.Response(200, json={"id": "direct-upstream", "status": "succeeded", "result_ref": "direct-result"})
            return httpx.Response(200, json={"id": "direct-upstream", "status": "queued"})
        if request.method in {"GET", "HEAD"} and path == "/direct/api/results/direct-result":
            self.result_reads += 1
            return _media_response(request, DIRECT_RESULT)
        raise AssertionError(f"unexpected direct provider request: {request.method} {path}")


class RecoverablePortalJobsAdapter(PortalJobsAdapter):
    supports_background_polling = True


class ManagedProviderTransport:
    """Stateful Ark-shaped provider; result bytes appear only after advance()."""

    def __init__(self) -> None:
        self.ready = False
        self.submits = 0
        self.downloads = 0
        self.authorization: list[str] = []

    def advance(self) -> None:
        self.ready = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v3/images/generations":
            self.submits += 1
            self.authorization.append(request.headers["authorization"])
            return httpx.Response(200, json={"data": [
                {"url": "https://media.volces.com/managed-a.png"},
                {"url": "https://media.volces.com/managed-b.png"},
            ]})
        if request.method == "GET" and request.url.host == "media.volces.com":
            self.downloads += 1
            if not self.ready:
                return httpx.Response(503)
            index = 0 if request.url.path == "/managed-a.png" else 1
            return httpx.Response(200, content=MANAGED_RESULTS[index], headers={"content-type": "image/png"})
        raise AssertionError(f"unexpected managed provider request: {request.method} {request.url}")


def _media_response(request: httpx.Request, body: bytes) -> httpx.Response:
    headers = {"content-type": "image/png", "accept-ranges": "bytes"}
    range_header = request.headers.get("range")
    if range_header:
        assert range_header == "bytes=0-7"
        headers.update({"content-range": f"bytes 0-7/{len(body)}", "content-length": "8"})
        return httpx.Response(206, stream=OneChunkStream(b"" if request.method == "HEAD" else body[:8]), headers=headers)
    headers["content-length"] = str(len(body))
    return httpx.Response(200, stream=OneChunkStream(b"" if request.method == "HEAD" else body), headers=headers)


def _headers(user_id: str, role: str = "user") -> dict[str, str]:
    timestamp = str(int(time.time()))
    username = user_id.replace("-", " ").title()
    payload = f"v2\n{timestamp}\n{user_id}\n{role}\n{quote(username, safe='')}"
    signature = hmac.new(SIGNING_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Portal-Sig-Version": "2",
        "X-Portal-Timestamp": timestamp,
        "X-Portal-User-Id": user_id,
        "X-Portal-Username": username,
        "X-Portal-Role": role,
        "X-Portal-Signature": signature,
        "Cookie": "portal_session=offline-only",
    }


def _managed_configuration(store: CanvasStore) -> tuple[ModelRouteDefinition, CredentialPool]:
    preset = trusted_route_presets()[("seedream", "ark")]
    model = LogicalModelDefinition(
        "managed-image", "Managed recovery fixture", "Offline restart integration.",
        "image", preset.operation_contracts,
    )
    route = ModelRouteDefinition(
        "managed-ark-route", model.model_id, preset.provider_id,
        preset.provider_model_name, preset.adapter_type, "managed-ark-pool",
        preset.family, preset.operation_contracts, 1, 2,
    )
    pool = CredentialPool(
        "managed-ark-pool", "ark", "official", ("seedream",),
        (CredentialKey("original", ORIGINAL_MANAGED_SECRET, 2),),
        hashlib.sha256(b"managed-ark-pool-v1").hexdigest(),
    )
    store.create_provider_definition(
        ProviderDefinition("ark", "Ark", "ark", "https://ark.cn-beijing.volces.com", "deployment-only"),
        actor_user_id="bootstrap",
    )
    store.create_logical_model(model)
    store.create_model_route(route)
    store.grant_model_access("managed-owner", model.model_id, actor_user_id="bootstrap")
    reference = store.assets_dir / "managed-reference.png"
    reference.write_bytes(REFERENCE)
    store.create_asset(
        asset_id="managed-reference", user_id="managed-owner", kind="reference",
        mime_type="image/png", relative_path="assets/managed-reference.png",
        size_bytes=len(REFERENCE), media_type="image",
    )
    return route, pool


@dataclass(frozen=True)
class RecoveryApp:
    app: object
    store: CanvasStore


def _build_app(
    data_dir: Path,
    clock: MutableClock,
    direct_provider: DirectProviderTransport,
    managed_provider: ManagedProviderTransport,
    pools: dict[str, CredentialPool],
) -> RecoveryApp:
    store = CanvasStore(data_dir, clock=clock)
    direct_registry = AdapterRegistry()
    direct_client = PortalClient(
        "http://127.0.0.1:46122",
        allowed_mounts=("/direct",),
        allowed_methods=("GET", "POST", "HEAD"),
        allow_loopback_http=True,
        transport=httpx.MockTransport(direct_provider),
    )
    direct_registry.register_generation(RecoverablePortalJobsAdapter(
        ServiceDeclaration("direct-fixture", "/direct", "image", ("image.generate",)),
        direct_client,
    ))
    coordinator = LocalExecutionCoordinator(global_limit=8, provider_limit=4, user_limit=2)
    route_factory = RouteAdapterFactory(
        data_dir=data_dir,
        asset_loader=_local_asset_loader(data_dir),
        provider_protocols={"ark": ProviderProtocol("ark", "ark", "https://ark.cn-beijing.volces.com")},
        transport=httpx.MockTransport(managed_provider),
    )
    runtime = ManagedRoutingRuntime(store, lambda: pools, RouteSelector(), coordinator, route_factory)
    app = create_app(
        Settings("test", 46121, data_dir, SIGNING_SECRET.decode(), allowed_origins=(ORIGIN,)),
        static_dir=data_dir / "missing-static",
        registry=direct_registry,
        model_catalog=ModelCatalog(direct_registry),
        canvas_store=store,
        managed_routing_runtime=runtime,
    )
    return RecoveryApp(app, store)


def _run_worker_until_idle(app: object, clock: MutableClock, limit: int = 12) -> None:
    for _ in range(limit):
        worked = asyncio.run(app.state.job_worker.run_once())
        clock.advance()
        if not worked:
            return
    raise AssertionError("background worker did not become idle")


def _assert_result(client: TestClient, headers: dict[str, str], url: str, expected: bytes) -> None:
    head = client.head(url, headers=headers)
    assert head.status_code == 200
    assert head.headers["content-type"] == "image/png"
    assert head.headers["content-length"] == str(len(expected))
    ranged = client.get(url, headers={**headers, "Range": "bytes=0-7"})
    assert ranged.status_code == 206 and ranged.content == expected[:8]
    full = client.get(url, headers=headers)
    assert full.status_code == 200 and full.content == expected


def test_background_worker_recovers_direct_and_managed_jobs_after_process_restart(tmp_path: Path) -> None:
    data_dir = tmp_path / "restart-data"
    clock = MutableClock()
    direct_provider = DirectProviderTransport()
    managed_provider = ManagedProviderTransport()
    initial_store = CanvasStore(data_dir, clock=clock)
    route, original_pool = _managed_configuration(initial_store)
    initial_store.set_usage_rates(video_price_fen=0, image_price_fen=37)

    first = _build_app(data_dir, clock, direct_provider, managed_provider, {original_pool.pool_id: original_pool})
    with TestClient(first.app, base_url=ORIGIN, raise_server_exceptions=False) as first_client:
        direct = first_client.post("/api/v1/jobs", headers=_headers("direct-owner"), json={
            "operation": "image.generate", "model_id": "direct-image",
            "prompt": "offline direct restart", "params": {}, "asset_ids": [],
            "idempotency_key": "direct-restart",
        })
        managed = first_client.post("/api/v1/jobs", headers=_headers("managed-owner"), json={
            "operation": "image.edit", "model_id": "managed-image",
            "prompt": "offline managed restart", "params": {}, "asset_ids": [],
            "inputs": {"reference_images": ["managed-reference"]},
            "idempotency_key": "managed-restart",
        })
        assert direct.status_code == 201 and direct.json()["status"] == "queued"
        assert managed.status_code == 201 and managed.json()["status"] == "queued"
        direct_job_id = direct.json()["id"]
        managed_job_id = managed.json()["id"]
        managed_before, forbidden = first.store.job_for_owner(managed_job_id, "managed-owner")
        assert managed_before is not None and forbidden is False
        route_snapshot = managed_before["route_snapshot_json"]
        expected_fingerprint = hashlib.sha256(ORIGINAL_MANAGED_SECRET.encode()).hexdigest()
        assert managed_before["key_fingerprint"] == expected_fingerprint
        assert managed_before["route_id"] == route.route_id

        unknown = first.store.reserve_job(
            user_id="direct-owner", job_id="unknown-job", service_id="direct-fixture",
            operation="image.generate", idempotency_key="unknown-restart", request_hash="a" * 64,
            model_id="direct-image",
        )
        first.store.mark_submission_unknown("unknown-job", str(unknown.job["submission_token"]))

    assert direct_provider.submits == 1
    assert managed_provider.submits == 1
    second = _build_app(
        data_dir, clock, direct_provider, managed_provider,
        {original_pool.pool_id: original_pool},
    )

    wrong_pool = CredentialPool(
        original_pool.pool_id, original_pool.provider_id, original_pool.group,
        original_pool.allowed_families,
        (CredentialKey("rotated", "offline-wrong-managed-secret", 2),),
        hashlib.sha256(b"managed-ark-pool-v2").hexdigest(),
    )
    wrong = _build_app(data_dir, clock, direct_provider, managed_provider, {wrong_pool.pool_id: wrong_pool})
    assert asyncio.run(wrong.app.state.job_worker.run_once()) is True
    clock.advance()
    assert asyncio.run(wrong.app.state.job_worker.run_once()) is True
    managed_delayed, _ = wrong.store.job_for_owner(managed_job_id, "managed-owner")
    assert managed_delayed is not None and managed_delayed["status"] == "queued"
    assert managed_provider.downloads == 0

    missing = _build_app(data_dir, clock, direct_provider, managed_provider, {})
    clock.advance()
    assert asyncio.run(missing.app.state.job_worker.run_once()) is True
    clock.advance()
    assert asyncio.run(missing.app.state.job_worker.run_once()) is True
    managed_delayed, _ = missing.store.job_for_owner(managed_job_id, "managed-owner")
    assert managed_delayed is not None and managed_delayed["status"] == "queued"
    assert managed_provider.downloads == 0

    direct_provider.advance()
    managed_provider.advance()
    catalog_gets_before_restart_worker = direct_provider.catalog_gets
    _run_worker_until_idle(second.app, clock)

    direct_done, direct_forbidden = second.store.job_for_owner(direct_job_id, "direct-owner")
    managed_done, managed_forbidden = second.store.job_for_owner(managed_job_id, "managed-owner")
    unknown_after, unknown_forbidden = second.store.job_for_owner("unknown-job", "direct-owner")
    assert direct_done is not None and direct_forbidden is False and direct_done["status"] == "succeeded"
    assert managed_done is not None and managed_forbidden is False and managed_done["status"] == "succeeded"
    stored_managed_results = json.loads(str(managed_done["result_ids_json"]))
    assert len(stored_managed_results) == 2
    assert stored_managed_results[0] == managed_done["result_id"]
    assert stored_managed_results[0] != stored_managed_results[1]
    assert managed_done["route_snapshot_json"] == route_snapshot
    assert managed_done["key_fingerprint"] == expected_fingerprint
    assert unknown_after is not None and unknown_forbidden is False
    assert unknown_after["status"] == "submission_unknown" and unknown_after["submission_state"] == "submission_unknown"
    assert direct_provider.catalog_gets == catalog_gets_before_restart_worker
    assert direct_provider.submits == 1 and managed_provider.submits == 1
    assert second.store.claim_job_acknowledgement() is None

    direct_polls_before_get = direct_provider.polls
    managed_downloads_before_get = managed_provider.downloads
    direct_owner = TestClient(second.app, base_url=ORIGIN, raise_server_exceptions=False)
    managed_owner = TestClient(second.app, base_url=ORIGIN, raise_server_exceptions=False)
    other = TestClient(second.app, base_url=ORIGIN, raise_server_exceptions=False)
    admin = TestClient(second.app, base_url=ORIGIN, raise_server_exceptions=False)

    direct_response = direct_owner.get(f"/api/v1/jobs/{direct_job_id}", headers=_headers("direct-owner"))
    managed_response = managed_owner.get(f"/api/v1/jobs/{managed_job_id}", headers=_headers("managed-owner"))
    assert direct_response.status_code == 200 and direct_response.json()["status"] == "succeeded"
    assert managed_response.status_code == 200 and managed_response.json()["status"] == "succeeded"
    assert len(managed_response.json()["results"]) == 2
    assert direct_provider.polls == direct_polls_before_get
    assert managed_provider.downloads == managed_downloads_before_get

    _assert_result(direct_owner, _headers("direct-owner"), direct_response.json()["results"][0]["url"], DIRECT_RESULT)
    for result, expected in zip(managed_response.json()["results"], MANAGED_RESULTS, strict=True):
        _assert_result(managed_owner, _headers("managed-owner"), result["url"], expected)

    for client, headers in (
        (other, _headers("unrelated-user")),
        (admin, _headers("canvas-admin", "admin")),
    ):
        assert client.get(f"/api/v1/jobs/{direct_job_id}", headers=headers).status_code == 404
        assert client.get(direct_response.json()["results"][0]["url"], headers=headers).status_code == 404
        assert client.get(f"/api/v1/jobs/{managed_job_id}", headers=headers).status_code == 404
        assert client.get(managed_response.json()["results"][0]["url"], headers=headers).status_code == 404

    direct_usage = direct_owner.get("/api/v1/usage", headers=_headers("direct-owner")).json()
    managed_usage = managed_owner.get("/api/v1/usage", headers=_headers("managed-owner")).json()
    assert len(direct_usage["jobs"]) == 1 and direct_usage["jobs"][0]["model_id"] == "direct-image"
    assert len(managed_usage["jobs"]) == 1 and managed_usage["jobs"][0]["model_id"] == "managed-image"
    assert direct_usage["summary"] == {"successful_jobs": 1, "image_count": 1, "video_seconds": 0, "total_cost_fen": "37"}
    assert managed_usage["summary"] == {"successful_jobs": 1, "image_count": 1, "video_seconds": 0, "total_cost_fen": "37"}
    assert direct_owner.get("/api/v1/admin/usage", headers=_headers("direct-owner")).status_code == 404
    assert admin.get("/api/v1/admin/usage", headers=_headers("canvas-admin", "admin")).status_code == 404
    all_usage = second.store.usage_for_all_users()
    assert sum(len(item["jobs"]) for item in all_usage) == 2

    assert direct_provider.submits == 1 and managed_provider.submits == 1
    assert direct_provider.polls == direct_polls_before_get
    assert managed_provider.downloads == managed_downloads_before_get

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
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
from ai_creation_canvas.domain.models import PortalUser, RequestContext
from ai_creation_canvas.job_polling import JobPollingService
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
    requires_portal_cookie = False
    requires_request_scoped_polling = False
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


class RequestScopedPortalTransport:
    def __init__(self, poll_response: httpx.Response | None = None) -> None:
        self.poll_response = poll_response or httpx.Response(
            200,
            json={"id": "request-upstream", "status": "succeeded", "result_ref": "request-result"},
        )
        self.submits = 0
        self.polls = 0
        self.poll_cookies: list[str] = []
        self.poll_started: threading.Event | None = None
        self.release_poll: threading.Event | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/portal/api/config":
            return httpx.Response(200, json={"models": [{
                "id": "portal-image",
                "display_name": "Request Portal",
                "operations": ["image.generate"],
                "input_media": ["text"],
                "parameter_schema": {},
            }]})
        if request.method == "POST" and request.url.path == "/portal/api/jobs":
            self.submits += 1
            return httpx.Response(201, json={"id": "request-upstream", "status": "queued"})
        if request.method == "GET" and request.url.path == "/portal/api/jobs/request-upstream":
            self.polls += 1
            self.poll_cookies.append(request.headers.get("cookie", ""))
            if self.poll_started is not None:
                self.poll_started.set()
            if self.release_poll is not None:
                assert self.release_poll.wait(timeout=2)
            return self.poll_response
        raise AssertionError(f"unexpected request-scoped Portal call: {request.method} {request.url.path}")


def _request_scoped_app(data_dir: Path, transport: RequestScopedPortalTransport):
    registry = AdapterRegistry()
    portal_client = PortalClient(
        "http://127.0.0.1:46122",
        allowed_mounts=("/portal",),
        allowed_methods=("GET", "POST"),
        allow_loopback_http=True,
        transport=httpx.MockTransport(transport),
    )
    registry.register_generation(PortalJobsAdapter(
        ServiceDeclaration("portal-fixture", "/portal", "image", ("image.generate",)),
        portal_client,
    ))
    store = CanvasStore(data_dir)
    app = create_app(
        Settings("test", 46121, data_dir, SIGNING_SECRET.decode(), allowed_origins=(ORIGIN,)),
        static_dir=data_dir / "missing-static",
        registry=registry,
        model_catalog=ModelCatalog(registry),
        canvas_store=store,
    )
    app.state.job_worker._idle_seconds = 3600.0
    return app, store


def _submit_request_scoped_job(client: TestClient, *, cookie: str) -> str:
    response = client.post(
        "/api/v1/jobs",
        headers={**_headers("request-owner"), "Cookie": cookie},
        json={
            "operation": "image.generate",
            "model_id": "portal-image",
            "prompt": "offline request polling",
            "params": {},
            "asset_ids": [],
            "idempotency_key": "request-job",
        },
    )
    assert response.status_code == 201 and response.json()["status"] == "queued"
    return str(response.json()["id"])


@pytest.mark.parametrize(
    ("poll_response", "error_code"),
    (
        (httpx.Response(400, json={"code": "denied"}), "REQUEST_REJECTED"),
        (
            httpx.Response(200, json={
                "id": "request-upstream",
                "status": "succeeded",
                "result_ref": "https://invalid.example/result",
            }),
            "INVALID_UPSTREAM_RESULT",
        ),
    ),
)
def test_request_scoped_terminal_and_invalid_polls_store_safe_failures(
    tmp_path: Path, poll_response: httpx.Response, error_code: str
) -> None:
    transport = RequestScopedPortalTransport(poll_response)
    app, store = _request_scoped_app(tmp_path / error_code, transport)
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        job_id = _submit_request_scoped_job(client, cookie="portal_session=terminal-owner")
        response = client.get(
            f"/api/v1/jobs/{job_id}",
            headers={**_headers("request-owner"), "Cookie": "portal_session=terminal-owner"},
        )

    assert response.status_code == 200
    assert response.json()["id"] == job_id
    assert response.json()["status"] == "failed"
    assert response.json()["error"]["code"] == error_code
    stored, forbidden = store.job_for_owner(job_id, "request-owner")
    assert stored is not None and forbidden is False
    assert stored["submission_token"] is None and stored["error_code"] == error_code


def test_request_scoped_temporary_poll_error_releases_lease_without_cookie_persistence(
    tmp_path: Path, caplog
) -> None:
    cookie = "portal_session=temporary-secret"
    transport = RequestScopedPortalTransport(httpx.Response(503))
    app, store = _request_scoped_app(tmp_path / "temporary", transport)
    with caplog.at_level(logging.WARNING):
        with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
            job_id = _submit_request_scoped_job(client, cookie=cookie)
            response = client.get(
                f"/api/v1/jobs/{job_id}",
                headers={**_headers("request-owner"), "Cookie": cookie},
            )

    assert response.status_code == 200 and response.json()["status"] == "queued"
    stored, forbidden = store.job_for_owner(job_id, "request-owner")
    assert stored is not None and forbidden is False
    assert stored["submission_token"] is None
    assert transport.poll_cookies == [cookie]
    assert cookie.encode() not in store.database.read_bytes()
    assert cookie not in caplog.text


def test_concurrent_owner_gets_execute_exactly_one_request_scoped_poll(tmp_path: Path) -> None:
    transport = RequestScopedPortalTransport()
    transport.poll_started = threading.Event()
    transport.release_poll = threading.Event()
    app, _ = _request_scoped_app(tmp_path / "concurrent", transport)
    cookie = "portal_session=concurrent-owner"
    headers = {**_headers("request-owner"), "Cookie": cookie}
    with (
        TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as first_client,
        TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as second_client,
    ):
        job_id = _submit_request_scoped_job(first_client, cookie=cookie)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(first_client.get, f"/api/v1/jobs/{job_id}", headers=headers)
            assert transport.poll_started.wait(timeout=2)
            second = second_client.get(f"/api/v1/jobs/{job_id}", headers=headers)
            transport.release_poll.set()
            completed = first.result(timeout=2)

    assert second.status_code == 200 and second.json()["status"] == "queued"
    assert completed.status_code == 200 and completed.json()["status"] == "succeeded"
    assert transport.polls == 1 and transport.poll_cookies == [cookie]


def test_request_scoped_restart_waits_for_fresh_authenticated_get(tmp_path: Path) -> None:
    data_dir = tmp_path / "request-restart"
    transport = RequestScopedPortalTransport()
    first_app, first_store = _request_scoped_app(data_dir, transport)
    with TestClient(first_app, base_url=ORIGIN, raise_server_exceptions=False) as first_client:
        job_id = _submit_request_scoped_job(
            first_client, cookie="portal_session=first-process"
        )
    assert transport.polls == 0
    first_row, _ = first_store.job_for_owner(job_id, "request-owner")
    assert first_row is not None and first_row["completion_mode"] == "request"
    assert b"portal_session=first-process" not in first_store.database.read_bytes()

    second_app, second_store = _request_scoped_app(data_dir, transport)
    with TestClient(second_app, base_url=ORIGIN, raise_server_exceptions=False) as second_client:
        assert transport.polls == 0
        completed = second_client.get(
            f"/api/v1/jobs/{job_id}",
            headers={
                **_headers("request-owner"),
                "Cookie": "portal_session=fresh-process",
            },
        )

    assert completed.status_code == 200 and completed.json()["status"] == "succeeded"
    assert transport.poll_cookies == ["portal_session=fresh-process"]
    second_row, _ = second_store.job_for_owner(job_id, "request-owner")
    assert second_row is not None and second_row["status"] == "succeeded"


def test_cancelled_request_scoped_poll_immediately_releases_its_lease(tmp_path: Path) -> None:
    class CancelledPortalAdapter:
        service_id = "cancelled-portal"
        requires_request_scoped_polling = True

        async def list_models(self, context):
            return ()

        async def submit(self, context, request):
            raise AssertionError("submission is outside this polling test")

        async def poll(self, context, upstream_job_id):
            raise AssertionError("background poll is forbidden")

        async def poll_with_cookie(self, context, upstream_job_id, cookie_header):
            raise asyncio.CancelledError

    store = CanvasStore(tmp_path / "cancelled")
    reservation = store.reserve_job(
        user_id="request-owner",
        job_id="cancelled-job",
        service_id="cancelled-portal",
        operation="image.generate",
        idempotency_key="cancelled-key",
        request_hash="c" * 64,
        completion_mode="request",
    )
    store.mark_submitted(
        "cancelled-job",
        "cancelled-upstream",
        "queued",
        str(reservation.job["submission_token"]),
    )
    claim = store.claim_request_scoped_job(
        "cancelled-job", user_id="request-owner", lease_seconds=30
    )
    assert claim is not None
    registry = AdapterRegistry()
    registry.register_generation(CancelledPortalAdapter())
    context = RequestContext(
        PortalUser("request-owner", "Request Owner", "user"), "request", "trace"
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(JobPollingService(store, registry).poll_request_claim(
            claim,
            str(claim["submission_token"]),
            context,
            "portal_session=current",
        ))

    stored, _ = store.job_for_owner("cancelled-job", "request-owner")
    assert stored is not None and stored["status"] == "queued"
    assert stored["submission_token"] is None
    assert stored["lease_until"] is not None and float(stored["lease_until"]) <= time.time()


def test_background_worker_recovers_direct_and_managed_jobs_after_process_restart(tmp_path: Path) -> None:
    data_dir = tmp_path / "restart-data"
    clock = MutableClock()
    direct_provider = DirectProviderTransport()
    managed_provider = ManagedProviderTransport()
    initial_store = CanvasStore(data_dir, clock=clock)
    route, original_pool = _managed_configuration(initial_store)
    initial_store.set_usage_rates(video_price_fen=0, image_price_fen=37)

    first = _build_app(data_dir, clock, direct_provider, managed_provider, {original_pool.pool_id: original_pool})
    # The first process owns a real worker lifecycle, but its long idle wait keeps
    # provider completion strictly on the reconstructed process below.
    first.app.state.job_worker._idle_seconds = 3600.0
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

    assert direct_provider.submits == 1 and direct_provider.polls == 0
    assert managed_provider.submits == 1 and managed_provider.downloads == 0
    assert managed_provider.authorization == [f"Bearer {ORIGINAL_MANAGED_SECRET}"]
    managed_upstream_id = str(managed_before["upstream_job_id"])
    pending_path = data_dir / "ark-results" / "pending.json"
    pending_before_recovery = json.loads(pending_path.read_text(encoding="utf-8"))
    assert pending_before_recovery[managed_upstream_id] == {
        "kind": "image",
        "urls": [
            "https://media.volces.com/managed-a.png",
            "https://media.volces.com/managed-b.png",
        ],
    }

    second = _build_app(
        data_dir, clock, direct_provider, managed_provider,
        {original_pool.pool_id: original_pool},
    )
    direct_adapter_before_get = second.app.state.adapter_registry.generation("direct-fixture")
    provider_counters_before_get = (
        direct_provider.catalog_gets,
        direct_provider.polls,
        managed_provider.submits,
        managed_provider.downloads,
        tuple(managed_provider.authorization),
    )
    queued_reader = TestClient(second.app, base_url=ORIGIN, raise_server_exceptions=False)
    direct_queued = queued_reader.get(f"/api/v1/jobs/{direct_job_id}", headers=_headers("direct-owner"))
    managed_queued = queued_reader.get(f"/api/v1/jobs/{managed_job_id}", headers=_headers("managed-owner"))
    queued_reader.close()
    assert direct_queued.status_code == 200 and direct_queued.json() == {"id": direct_job_id, "status": "queued"}
    assert managed_queued.status_code == 200 and managed_queued.json() == {"id": managed_job_id, "status": "queued"}
    assert (
        direct_provider.catalog_gets,
        direct_provider.polls,
        managed_provider.submits,
        managed_provider.downloads,
        tuple(managed_provider.authorization),
    ) == provider_counters_before_get
    assert second.app.state.adapter_registry.generation("direct-fixture") is direct_adapter_before_get

    wrong_pool = CredentialPool(
        original_pool.pool_id, original_pool.provider_id, original_pool.group,
        original_pool.allowed_families,
        (CredentialKey("rotated", "offline-wrong-managed-secret", 2),),
        hashlib.sha256(b"managed-ark-pool-v2").hexdigest(),
    )
    wrong = _build_app(data_dir, clock, direct_provider, managed_provider, {wrong_pool.pool_id: wrong_pool})

    # Keep the direct job in the same database but beyond this credential test's
    # retry window, so only the exact managed job can satisfy run_once().
    for _ in range(2):
        claim = wrong.store.claim_pollable_job()
        assert claim is not None
        if claim["id"] == direct_job_id:
            direct_retry_boundary = clock.value + 600.0
            wrong.store.release_job_lease(
                direct_job_id,
                token=str(claim["submission_token"]),
                retry_after_seconds=600.0,
            )
            break
        assert claim["id"] == managed_job_id
        wrong.store.release_job_lease(
            managed_job_id,
            token=str(claim["submission_token"]),
            retry_after_seconds=0,
        )
    else:
        raise AssertionError("direct job was not isolated from managed recovery")

    wrong_attempt_at = clock.value
    assert asyncio.run(wrong.app.state.job_worker.run_once()) is True
    managed_wrong_attempt, _ = wrong.store.job_for_owner(managed_job_id, "managed-owner")
    assert managed_wrong_attempt is not None
    assert managed_wrong_attempt["status"] == "queued"
    assert managed_wrong_attempt["submission_token"] is None
    assert managed_wrong_attempt["lease_until"] == wrong_attempt_at + 2.0
    assert asyncio.run(wrong.app.state.job_worker.run_once()) is False
    assert managed_provider.downloads == 0
    assert managed_provider.authorization == [f"Bearer {ORIGINAL_MANAGED_SECRET}"]

    missing = _build_app(data_dir, clock, direct_provider, managed_provider, {})
    clock.advance(2.001)
    missing_attempt_at = clock.value
    assert asyncio.run(missing.app.state.job_worker.run_once()) is True
    managed_missing_attempt, _ = missing.store.job_for_owner(managed_job_id, "managed-owner")
    assert managed_missing_attempt is not None
    assert managed_missing_attempt["status"] == "queued"
    assert managed_missing_attempt["submission_token"] is None
    assert managed_missing_attempt["lease_until"] == missing_attempt_at + 2.0
    assert asyncio.run(missing.app.state.job_worker.run_once()) is False
    assert managed_provider.downloads == 0
    assert managed_provider.authorization == [f"Bearer {ORIGINAL_MANAGED_SECRET}"]

    direct_provider.advance()
    managed_provider.advance()
    catalog_gets_before_restart_worker = direct_provider.catalog_gets
    clock.advance(2.001)
    assert asyncio.run(second.app.state.job_worker.run_once()) is True
    managed_after_cas, _ = second.store.job_for_owner(managed_job_id, "managed-owner")
    assert managed_after_cas is not None and managed_after_cas["status"] == "succeeded"
    assert managed_upstream_id in json.loads(pending_path.read_text(encoding="utf-8"))
    assert asyncio.run(second.app.state.job_worker.run_once()) is True
    pending_after_ack = json.loads(pending_path.read_text(encoding="utf-8"))
    assert managed_upstream_id not in pending_after_ack
    assert second.store.claim_job_acknowledgement() is None
    assert asyncio.run(second.app.state.job_worker.run_once()) is False
    assert managed_provider.downloads == 2
    assert managed_provider.authorization == [f"Bearer {ORIGINAL_MANAGED_SECRET}"]

    clock.advance(direct_retry_boundary - clock.value + 0.001)
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

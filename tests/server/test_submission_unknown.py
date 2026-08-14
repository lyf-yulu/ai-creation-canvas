from __future__ import annotations

import json
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from tests.server.test_jobs_model_routes import ScriptedCoordinator, build_app, headers, payload
from ai_creation_canvas.coordination import CredentialLease
from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import JobState, UpstreamJob
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.storage.sqlite import CanvasStore
from tests.contracts.test_generation_flow import FakeGeneration, headers as direct_headers


def test_credential_lease_repr_does_not_expose_key_identity_or_secret() -> None:
    rendered = repr(CredentialLease("route-a", "pool-a", "sensitive-key-id", "sensitive-secret", "fingerprint", "owner"))
    assert "sensitive-key-id" not in rendered
    assert "sensitive-secret" not in rendered


def test_possible_send_timeout_is_visible_unknown_and_never_replayed(tmp_path: Path) -> None:
    attempts = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("body timeout", request=request)
    app, store, _ = build_app(tmp_path, handler, ScriptedCoordinator())
    client = TestClient(app, raise_server_exceptions=False)

    first = client.post("/api/v1/jobs", headers=headers(), json=payload())
    repeated = client.post("/api/v1/jobs", headers=headers(), json=payload())
    conflict = client.post("/api/v1/jobs", headers=headers(), json=payload(prompt="different"))

    assert first.status_code == repeated.status_code == 201
    assert first.json() == repeated.json()
    assert first.json()["status"] == "submission_unknown"
    assert conflict.status_code == 409
    assert attempts == 1
    item, _ = store.job_for_owner(first.json()["id"], "user-a")
    assert item is not None and item["submission_state"] == "submission_unknown"
    assert item["submission_token"] is None and item["lease_until"] is None
    assert store.claim_pollable_job(lease_seconds=30) is None
    encoded = json.dumps(item, sort_keys=True)
    assert "secret-value" not in encoded and "gemini-a" not in encoded


def test_concurrent_identical_requests_reserve_once_and_submit_once(tmp_path: Path) -> None:
    attempts = 0
    barrier = threading.Barrier(2)
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"data": [{"b64_json": "iVBORw0KGgptYW5hZ2VkLXJlc3VsdA=="}]})
    app, store, _ = build_app(tmp_path, handler, ScriptedCoordinator())
    results: list[tuple[int, dict[str, object]]] = []
    def submit() -> None:
        with TestClient(app, raise_server_exceptions=False) as client:
            barrier.wait()
            response = client.post("/api/v1/jobs", headers=headers(), json=payload())
            results.append((response.status_code, response.json()))
    threads = [threading.Thread(target=submit), threading.Thread(target=submit)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()

    assert [status for status, _ in results] == [201, 201]
    assert len({body["id"] for _, body in results}) == 1
    assert attempts == 1
    with store._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM canvas_jobs").fetchone()[0] == 1


def test_in_flight_submission_crossing_reservation_lease_is_never_reclaimed(tmp_path: Path) -> None:
    now = [100.0]
    store = CanvasStore(tmp_path / "clocked-data", clock=lambda: now[0])
    entered = threading.Event()
    release = threading.Event()
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        entered.set()
        assert release.wait(timeout=5)
        return httpx.Response(200, json={"data": [{"b64_json": "iVBORw0KGgptYW5hZ2VkLXJlc3VsdA=="}]})

    app, store, _ = build_app(tmp_path, handler, ScriptedCoordinator(), store=store)
    first_result: list[tuple[int, dict[str, object]]] = []

    def first_submit() -> None:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/api/v1/jobs", headers=headers(), json=payload())
            first_result.append((response.status_code, response.json()))

    thread = threading.Thread(target=first_submit)
    thread.start()
    assert entered.wait(timeout=5)
    now[0] += 31
    with TestClient(app, raise_server_exceptions=False) as client:
        repeated = client.post("/api/v1/jobs", headers=headers(), json=payload())
        conflict = client.post("/api/v1/jobs", headers=headers(), json=payload(prompt="changed"))
    release.set()
    thread.join(timeout=5)

    assert repeated.status_code == 201 and repeated.json()["status"] == "submission_unknown"
    assert conflict.status_code == 409
    assert attempts == 1
    assert first_result and first_result[0][1]["status"] == "submission_unknown"
    item, _ = store.job_for_owner(repeated.json()["id"], "user-a")
    assert item is not None and item["submission_token"] is None
    assert item["submission_state"] == "submission_unknown"


def test_direct_in_flight_submission_crossing_lease_and_restart_never_replays(tmp_path: Path) -> None:
    now = [100.0]
    data_dir = tmp_path / "direct-clocked-data"
    entered = threading.Event()
    release = threading.Event()

    class BlockingGeneration(FakeGeneration):
        async def submit(self, context, request):
            self.submit_count += 1
            entered.set()
            assert release.wait(timeout=5)
            return UpstreamJob(
                self.service_id,
                "upstream-1",
                JobState("upstream-1", "queued"),
            )

    adapter = BlockingGeneration()
    registry = AdapterRegistry()
    registry.register_generation(adapter)
    settings = Settings("test", 8992, data_dir, "test-secret")
    first_store = CanvasStore(data_dir, clock=lambda: now[0])
    first_app = create_app(
        settings,
        registry=registry,
        model_catalog=ModelCatalog(registry),
        canvas_store=first_store,
    )
    request_payload = {
        "operation": "image.generate",
        "model_id": "model-1",
        "prompt": "safe prompt",
        "params": {},
        "asset_ids": [],
        "idempotency_key": "direct-crash-window",
    }
    first_result: list[tuple[int, dict[str, object]]] = []

    def first_submit() -> None:
        response = TestClient(first_app, raise_server_exceptions=False).post(
            "/api/v1/jobs", json=request_payload, headers=direct_headers()
        )
        first_result.append((response.status_code, response.json()))

    thread = threading.Thread(target=first_submit)
    thread.start()
    assert entered.wait(timeout=5)
    now[0] += 31
    reopened = CanvasStore(data_dir, clock=lambda: now[0])
    retry_app = create_app(
        settings,
        registry=registry,
        model_catalog=ModelCatalog(registry),
        canvas_store=reopened,
    )
    repeated = TestClient(retry_app, raise_server_exceptions=False).post(
        "/api/v1/jobs", json=request_payload, headers=direct_headers()
    )
    release.set()
    thread.join(timeout=5)

    assert repeated.status_code == 201
    assert repeated.json()["status"] == "submission_unknown"
    assert adapter.submit_count == 1
    assert first_result == [(201, repeated.json())]
    stored, _ = reopened.job_for_owner(repeated.json()["id"], "u-a")
    assert stored is not None
    assert stored["submission_state"] == "submission_unknown"
    assert stored["submission_token"] is None


def test_get_expires_stale_in_flight_to_submission_unknown(tmp_path: Path) -> None:
    now = [100.0]
    store = CanvasStore(tmp_path / "get-clock-data", clock=lambda: now[0])
    entered = threading.Event()
    release = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        assert release.wait(timeout=5)
        return httpx.Response(200, json={"data": [{"b64_json": "iVBORw0KGgptYW5hZ2VkLXJlc3VsdA=="}]})

    app, _, _ = build_app(tmp_path, handler, ScriptedCoordinator(), store=store)
    created: list[dict[str, object]] = []

    def submit() -> None:
        response = TestClient(app, raise_server_exceptions=False).post("/api/v1/jobs", headers=headers(), json=payload())
        created.append(response.json())

    thread = threading.Thread(target=submit)
    thread.start()
    assert entered.wait(timeout=5)
    with store._connection() as db:
        job_id = str(db.execute("SELECT id FROM canvas_jobs").fetchone()[0])
    now[0] += 31
    response = TestClient(app, raise_server_exceptions=False).get(f"/api/v1/jobs/{job_id}", headers=headers())
    release.set()
    thread.join(timeout=5)
    assert response.status_code == 200 and response.json()["status"] == "submission_unknown"
    assert created and created[0]["status"] == "submission_unknown"


def test_lost_snapshot_cas_never_calls_provider(tmp_path: Path, monkeypatch) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise AssertionError("provider must not be called after losing ownership")

    app, store, _ = build_app(tmp_path, handler, ScriptedCoordinator())
    original = store.record_routing_snapshot

    def lose_ownership(*args, **kwargs):
        item = original(*args, **kwargs)
        return {**item, "submission_token": "another-owner", "submission_state": "in_flight"}

    monkeypatch.setattr(store, "record_routing_snapshot", lose_ownership)
    response = TestClient(app, raise_server_exceptions=False).post("/api/v1/jobs", headers=headers(), json=payload())

    assert response.status_code == 201
    assert attempts == 0


def test_submitted_state_is_persisted_before_credential_lease_exit(tmp_path: Path) -> None:
    observed: list[str] = []

    class ExitObservingCoordinator(ScriptedCoordinator):
        store = None

        @asynccontextmanager
        async def acquire_credential(self, job_id, user_id, candidate):
            key = candidate.pool.keys[0]
            yield CredentialLease(candidate.route.route_id, candidate.pool.pool_id, key.key_id, key.secret, self.fingerprint_secret(key.secret), "owner")
            item, _ = self.store.job_for_owner(job_id, user_id)
            observed.append(str(item["submission_state"]))
            raise RuntimeError("simulated lease exit crash")

    coordinator = ExitObservingCoordinator()
    app, store, _ = build_app(
        tmp_path,
        lambda request: httpx.Response(200, json={"data": [{"b64_json": "iVBORw0KGgptYW5hZ2VkLXJlc3VsdA=="}]}),
        coordinator,
    )
    coordinator.store = store
    client = TestClient(app, raise_server_exceptions=False)
    first = client.post("/api/v1/jobs", headers=headers(), json=payload())
    repeated = client.post("/api/v1/jobs", headers=headers(), json=payload())

    assert observed == ["submitted"]
    assert first.status_code == repeated.status_code == 201
    assert first.json()["id"] == repeated.json()["id"]

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import logging
import time
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import AssetRef, JobState, JobStatus, ModelSpec, UpstreamJob
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.errors import ApiError, PortalUpstreamError
from ai_creation_canvas.storage.sqlite import CanvasStore
from tests.server.test_managed_job_adapter import _runtime


def _asset(asset_id: str) -> AssetRef:
    return AssetRef(asset_id, "reference", "active", "video/mp4")


class RecoverableGeneration:
    service_id = "recoverable-video"
    requires_portal_cookie = False
    supports_background_polling = True

    def __init__(self, outcome: object | None = None, *, before_return=None) -> None:
        self.outcome = outcome or JobState("upstream-video", "succeeded", results=(_asset("result_video"),))
        self.before_return = before_return
        self.poll_count = 0
        self.acknowledged: list[str] = []
        self.contexts = []

    async def list_models(self, context):
        del context
        return (ModelSpec("video-model", self.service_id, "Video", ("video.generate",)),)

    async def submit(self, context, request):
        del context, request
        return UpstreamJob(self.service_id, "upstream-video", JobState("upstream-video", "queued"), datetime.now(UTC))

    async def poll(self, context, upstream_job_id):
        self.contexts.append(context)
        self.poll_count += 1
        if self.before_return is not None:
            self.before_return()
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def acknowledge_poll_result(self, upstream_job_id: str) -> None:
        self.acknowledged.append(upstream_job_id)


def _submitted(
    store: CanvasStore,
    *,
    job_id: str = "job-a",
    service_id: str = "recoverable-video",
    status: str = "queued",
) -> None:
    reservation = store.reserve_job(
        user_id="user-a",
        job_id=job_id,
        service_id=service_id,
        operation="video.generate",
        idempotency_key=f"key-{job_id}",
        request_hash=f"hash-{job_id}",
    )
    store.mark_submitted(job_id, f"upstream-{job_id}", status, str(reservation.job["submission_token"]))


def _registry(adapter: RecoverableGeneration | None = None) -> AdapterRegistry:
    registry = AdapterRegistry()
    if adapter is not None:
        registry.register_generation(adapter)
    return registry


def _worker(store, registry, *, managed_runtime=None, lease_seconds: float = 30.0, idle_seconds: float = 0.01):
    """Build the scheduler with its app-owned polling service."""
    from ai_creation_canvas.job_polling import JobPollingService
    from ai_creation_canvas.job_worker import JobWorker

    service = JobPollingService(store, registry, managed_runtime)
    return JobWorker(store, service, lease_seconds=lease_seconds, idle_seconds=idle_seconds)


def _stored(store: CanvasStore, job_id: str = "job-a") -> dict[str, object]:
    item, forbidden = store.job_for_owner(job_id, "user-a")
    assert item is not None and forbidden is False
    return item


def test_startup_worker_persists_every_result_in_provider_order(tmp_path) -> None:
    store = CanvasStore(tmp_path / "data")
    _submitted(store)
    adapter = RecoverableGeneration(
        JobState(
            "upstream-job-a",
            "succeeded",
            results=(_asset("result_first"), _asset("result_second")),
        )
    )
    registry = _registry(adapter)
    app = create_app(
        Settings("test", 8992, tmp_path / "data", "test-secret"),
        static_dir=tmp_path / "dist",
        registry=registry,
        model_catalog=ModelCatalog(registry),
        canvas_store=store,
    )

    with TestClient(app, raise_server_exceptions=False):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and _stored(store)["status"] != "succeeded":
            time.sleep(0.01)

    item = _stored(store)
    assert item["status"] == "succeeded"
    assert json.loads(str(item["result_ids_json"])) == ["result_first", "result_second"]
    assert item["result_id"] == "result_first"
    assert adapter.poll_count == 1
    assert adapter.contexts[0].user.user_id == "user-a"


@pytest.mark.anyio
async def test_worker_polls_managed_job_through_saved_route_and_exact_fingerprint(tmp_path) -> None:
    runtime, _context, _item, coordinator = _runtime(tmp_path)
    adapter = RecoverableGeneration(
        JobState(
            "upstream-job",
            "succeeded",
            results=(_asset("managed_first"), _asset("managed_second")),
        )
    )

    class Factory:
        def __init__(self) -> None:
            self.leases = []

        def build(self, route, lease):
            del route
            self.leases.append(lease)
            return adapter

    factory = Factory()
    runtime = replace(runtime, adapter_factory=factory)
    worker = _worker(runtime.store, AdapterRegistry(), managed_runtime=runtime)

    assert await worker.run_once() is True

    item, forbidden = runtime.store.job_for_owner("managed-job", "user-a")
    assert item is not None and forbidden is False
    assert item["status"] == "succeeded"
    assert json.loads(str(item["result_ids_json"])) == ["managed_first", "managed_second"]
    assert [lease.key_id for lease in factory.leases] == ["original"]
    assert len(coordinator.candidates) == 1
    assert [key.key_id for key in coordinator.candidates[0].pool.keys] == ["original"]


@pytest.mark.anyio
async def test_missing_managed_credential_delays_without_rotating_to_another_key(tmp_path) -> None:
    runtime, _context, _item, coordinator = _runtime(tmp_path, include_original_key=False)

    class NeverFactory:
        def build(self, route, lease):
            raise AssertionError((route, lease))

    runtime = replace(runtime, adapter_factory=NeverFactory())
    worker = _worker(runtime.store, AdapterRegistry(), managed_runtime=runtime)

    assert await worker.run_once() is True

    item, forbidden = runtime.store.job_for_owner("managed-job", "user-a")
    assert item is not None and forbidden is False
    assert item["status"] == "queued"
    assert item["submission_token"] is None
    assert item["lease_until"] is not None
    assert coordinator.candidates == []


@pytest.mark.anyio
async def test_retryable_provider_error_releases_the_lease_for_a_delayed_retry(tmp_path) -> None:
    store = CanvasStore(tmp_path / "data")
    _submitted(store, status="running")
    adapter = RecoverableGeneration(PortalUpstreamError("UPSTREAM_TIMEOUT", retryable=True))
    worker = _worker(store, _registry(adapter))

    assert await worker.run_once() is True

    item = _stored(store)
    assert item["status"] == "running"
    assert item["submission_token"] is None
    assert item["lease_until"] is not None
    assert item["error_code"] is None


@pytest.mark.anyio
async def test_local_recovery_io_retries_then_succeeds_without_losing_pending_state(tmp_path, caplog) -> None:
    import base64
    import httpx

    from ai_creation_canvas.adapters.chiyun import ChiyunGenerationAdapter
    from ai_creation_canvas.domain.models import JobRequest
    from ai_creation_canvas.errors import InvalidUpstreamResult
    from tests.contracts.test_chiyun_adapter import PNG, context, model, provider

    class Clock:
        value = 1_000.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()
    adapter = ChiyunGenerationAdapter(
        provider=provider(), models=(model(),), api_key="test-only-secret", data_dir=tmp_path,
        asset_loader=lambda _: (PNG, "image/png"),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            "data": [{"b64_json": base64.b64encode(PNG).decode()}],
        })),
    )
    upstream = await adapter.submit(context(), JobRequest(
        "image.edit", "chiyun-gpt-image-2", "recover", "worker-local-io",
        {"ratio": "auto", "output_count": 1}, inputs={"reference_images": ("one",)},
    ))
    store = CanvasStore(tmp_path / "worker-data", clock=clock)
    reservation = store.reserve_job(
        user_id="user-a", job_id="local-io-job", service_id=adapter.service_id,
        operation="image.edit", idempotency_key="local-io-job", request_hash="l" * 64,
    )
    store.mark_submitted(
        "local-io-job", upstream.upstream_job_id, "queued",
        str(reservation.job["submission_token"]),
    )
    original_read = adapter._read_index
    read_count = 0

    def flaky_read():
        nonlocal read_count
        read_count += 1
        if read_count == 1:
            raise OSError("private local recovery failure")
        return original_read()

    adapter._read_index = flaky_read
    worker = _worker(store, _registry(adapter))

    with caplog.at_level(logging.WARNING):
        assert await worker.run_once() is True
    item, forbidden = store.job_for_owner("local-io-job", "user-a")
    assert item is not None and forbidden is False
    assert item["status"] == "queued"
    assert item["submission_token"] is None
    assert item["lease_until"] is not None
    assert "private local recovery failure" not in caplog.text

    clock.value += 3
    assert await worker.run_once() is True
    assert store.job_for_owner("local-io-job", "user-a")[0]["status"] == "succeeded"
    assert await worker.run_once() is True
    assert await worker.run_once() is False
    with pytest.raises(InvalidUpstreamResult):
        await adapter.poll(context(), upstream.upstream_job_id)


@pytest.mark.anyio
async def test_nonretryable_provider_error_is_terminal_and_does_not_log_exception_text(tmp_path, caplog) -> None:
    store = CanvasStore(tmp_path / "data")
    _submitted(store)
    private_text = "private-provider-error-text"
    adapter = RecoverableGeneration(PortalUpstreamError(private_text, retryable=False, status_code=400))
    worker = _worker(store, _registry(adapter))

    with caplog.at_level(logging.WARNING):
        assert await worker.run_once() is True

    item = _stored(store)
    assert item["status"] == "failed"
    assert item["error_code"] == "REQUEST_REJECTED"
    assert private_text not in caplog.text


@pytest.mark.anyio
async def test_provider_terminal_failure_is_persisted(tmp_path) -> None:
    store = CanvasStore(tmp_path / "data")
    _submitted(store, status="running")
    error = ApiError("TASK_FAILED", "The generation task failed.", False, "provider", "generation")
    adapter = RecoverableGeneration(JobState("upstream-job-a", "failed", error=error))
    worker = _worker(store, _registry(adapter))

    assert await worker.run_once() is True

    item = _stored(store)
    assert item["status"] == "failed"
    assert item["error_code"] == "TASK_FAILED"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "results",
    (
        (),
        (_asset("safe_first"), _asset("unsafe/result")),
        (_asset("duplicate"), _asset("duplicate")),
        tuple(_asset(f"result_{index}") for index in range(16)),
    ),
    ids=("empty", "invalid-later-id", "duplicate", "over-limit"),
)
async def test_invalid_provider_success_fails_terminally(tmp_path, results) -> None:
    store = CanvasStore(tmp_path / "data")
    _submitted(store)
    raw_state = SimpleNamespace(
        status=JobStatus.SUCCEEDED,
        result=results[0] if results else None,
        results=results,
        error=None,
    )
    adapter = RecoverableGeneration(raw_state)
    worker = _worker(store, _registry(adapter))

    assert await worker.run_once() is True

    item = _stored(store)
    assert item["status"] == "failed"
    assert item["error_code"] == "INVALID_UPSTREAM_RESULT"
    assert item["result_id"] is None


@pytest.mark.anyio
async def test_submission_unknown_is_never_claimed_by_the_worker(tmp_path) -> None:
    store = CanvasStore(tmp_path / "data")
    reservation = store.reserve_job(
        user_id="user-a",
        job_id="job-a",
        service_id="recoverable-video",
        operation="video.generate",
        idempotency_key="key-job-a",
        request_hash="hash-job-a",
    )
    store.mark_submission_unknown("job-a", str(reservation.job["submission_token"]))
    adapter = RecoverableGeneration()
    worker = _worker(store, _registry(adapter))

    assert await worker.run_once() is False
    assert adapter.poll_count == 0
    assert _stored(store)["status"] == "submission_unknown"


@pytest.mark.anyio
async def test_stale_poll_token_never_acknowledges_provider_pending_state(tmp_path) -> None:
    class Clock:
        value = 1_000.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()
    store = CanvasStore(tmp_path / "data", clock=clock)
    _submitted(store)
    replacement = None

    def reclaim() -> None:
        nonlocal replacement
        clock.value += 2
        replacement = store.claim_pollable_job(lease_seconds=30)

    adapter = RecoverableGeneration(before_return=reclaim)
    worker = _worker(store, _registry(adapter), lease_seconds=1)

    assert await worker.run_once() is True

    item = _stored(store)
    assert replacement is not None
    assert item["status"] == "queued"
    assert item["submission_token"] == replacement["submission_token"]
    assert adapter.acknowledged == []


@pytest.mark.anyio
async def test_successful_current_token_acknowledges_exactly_once(tmp_path) -> None:
    store = CanvasStore(tmp_path / "data")
    _submitted(store)
    adapter = RecoverableGeneration()
    worker = _worker(store, _registry(adapter))

    assert await worker.run_once() is True
    assert adapter.acknowledged == []
    assert await worker.run_once() is True
    assert await worker.run_once() is False

    assert _stored(store)["status"] == "succeeded"
    assert adapter.acknowledged == ["upstream-job-a"]


@pytest.mark.anyio
async def test_acknowledgement_failure_survives_restart_without_polling_provider_again(tmp_path, caplog) -> None:
    class Clock:
        value = 1_000.0

        def __call__(self) -> float:
            return self.value

    class FailsFirstAcknowledgement(RecoverableGeneration):
        def __init__(self) -> None:
            super().__init__()
            self.ack_attempts = 0

        async def acknowledge_poll_result(self, upstream_job_id: str) -> None:
            self.ack_attempts += 1
            if self.ack_attempts == 1:
                raise OSError("private acknowledgement failure")
            await super().acknowledge_poll_result(upstream_job_id)

    clock = Clock()
    data_dir = tmp_path / "data"
    store = CanvasStore(data_dir, clock=clock)
    _submitted(store)
    adapter = FailsFirstAcknowledgement()

    assert await _worker(store, _registry(adapter)).run_once() is True
    assert _stored(store)["status"] == "succeeded"
    assert adapter.poll_count == 1
    assert adapter.ack_attempts == 0

    reopened = CanvasStore(data_dir, clock=clock)
    with caplog.at_level(logging.WARNING):
        assert await _worker(reopened, _registry(adapter)).run_once() is True
    assert adapter.ack_attempts == 1
    assert adapter.poll_count == 1
    assert "private acknowledgement failure" not in caplog.text

    clock.value += 3
    restarted = CanvasStore(data_dir, clock=clock)
    retry_worker = _worker(restarted, _registry(adapter))
    assert await retry_worker.run_once() is True
    assert await retry_worker.run_once() is False
    assert adapter.ack_attempts == 2
    assert adapter.acknowledged == ["upstream-job-a"]
    assert adapter.poll_count == 1


@pytest.mark.anyio
async def test_worker_cancellation_releases_durable_acknowledgement_lease(tmp_path) -> None:
    class BlockingAcknowledgement(RecoverableGeneration):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()

        async def acknowledge_poll_result(self, upstream_job_id: str) -> None:
            del upstream_job_id
            self.started.set()
            await asyncio.Event().wait()

    store = CanvasStore(tmp_path / "data")
    _submitted(store)
    adapter = BlockingAcknowledgement()
    worker = _worker(store, _registry(adapter))
    assert await worker.run_once() is True
    running = asyncio.create_task(worker.run_once())
    await adapter.started.wait()
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running

    reclaimed = store.claim_job_acknowledgement(lease_seconds=30)
    assert reclaimed is not None
    assert reclaimed["id"] == "job-a"
    assert adapter.poll_count == 1


@pytest.mark.anyio
async def test_worker_renews_its_lease_while_a_slow_result_download_is_in_progress(tmp_path) -> None:
    class SlowGeneration(RecoverableGeneration):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def poll(self, context, upstream_job_id):
            self.contexts.append(context)
            self.poll_count += 1
            self.started.set()
            await self.release.wait()
            return self.outcome

    store = CanvasStore(tmp_path / "data")
    _submitted(store, status="running")
    adapter = SlowGeneration()
    worker = _worker(store, _registry(adapter), lease_seconds=0.06)
    running = asyncio.create_task(worker.run_once())
    await adapter.started.wait()
    await asyncio.sleep(0.09)
    assert store.claim_pollable_job(lease_seconds=0.06) is None
    adapter.release.set()
    assert await running is True


@pytest.mark.anyio
async def test_worker_cancellation_releases_the_current_lease(tmp_path) -> None:
    class BlockingGeneration(RecoverableGeneration):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()

        async def poll(self, context, upstream_job_id):
            del context, upstream_job_id
            self.started.set()
            await asyncio.Event().wait()

    store = CanvasStore(tmp_path / "data")
    _submitted(store)
    adapter = BlockingGeneration()
    worker = _worker(store, _registry(adapter))
    running = asyncio.create_task(worker.run_once())
    await adapter.started.wait()
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running

    reclaimed = store.claim_pollable_job(lease_seconds=30)
    assert reclaimed is not None and reclaimed["id"] == "job-a"


@pytest.mark.anyio
async def test_worker_survives_a_transient_storage_claim_failure(tmp_path) -> None:
    store = CanvasStore(tmp_path / "data")
    _submitted(store)

    class FailsOnce:
        def __init__(self, target):
            self.target = target
            self.failed = False

        def claim_pollable_job(self, **kwargs):
            if not self.failed:
                self.failed = True
                raise RuntimeError("transient storage failure")
            return self.target.claim_pollable_job(**kwargs)

        def __getattr__(self, name):
            return getattr(self.target, name)

    adapter = RecoverableGeneration()
    wrapped = FailsOnce(store)
    worker = _worker(wrapped, _registry(adapter))
    await worker.start()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and _stored(store)["status"] != "succeeded":
        await asyncio.sleep(0.01)
    await worker.stop()
    await worker.stop()

    assert _stored(store)["status"] == "succeeded"

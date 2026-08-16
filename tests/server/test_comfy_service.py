from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_creation_canvas.app import create_app
from ai_creation_canvas.comfy.service import (
    ComfyHttpWorkflowService,
    ComfyPromptOwnershipStore,
    ComfyServiceDeclaration,
    ComfyServiceStatus,
    ComfyWorkflowRequest,
)
from ai_creation_canvas.domain.models import JobStatus, PortalUser, RequestContext
from ai_creation_canvas.config import Settings
from ai_creation_canvas.errors import InvalidUpstreamResult, PortalUpstreamError
from ai_creation_canvas.storage.sqlite import CanvasStore


API_WORKFLOW = {"1": {"class_type": "LoadImage", "inputs": {}}, "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}}}
pytestmark = pytest.mark.anyio


def context(user_id: str = "user-1", request_id: str = "request-1") -> RequestContext:
    return RequestContext(PortalUser(user_id, "Ada", "user"), request_id, "trace-1")


def mock_transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


class _InMemoryPromptOwners:
    def __init__(self) -> None:
        self._owners: dict[tuple[str, str], str] = {}

    def record_comfy_prompt_owner(
        self, *, service_id: str, prompt_id: str, user_id: str, idempotency_key: str
    ) -> bool:
        del idempotency_key
        return self._owners.setdefault((service_id, prompt_id), user_id) == user_id

    def comfy_prompt_owner(self, service_id: str, prompt_id: str) -> str | None:
        return self._owners.get((service_id, prompt_id))


def _adapter(
    declaration: ComfyServiceDeclaration,
    *,
    prompt_owner_store: ComfyPromptOwnershipStore | None = None,
    **kwargs: object,
) -> ComfyHttpWorkflowService:
    return ComfyHttpWorkflowService(
        declaration, prompt_owner_store=prompt_owner_store or _InMemoryPromptOwners(), **kwargs
    )


async def test_adapter_reports_node_inventory_and_never_accepts_callers_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url == httpx.URL("http://127.0.0.1:8188/object_info")
        return httpx.Response(200, json={"LoadImage": {}, "SaveImage": {}}, request=request)

    adapter = _adapter(
        ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None),
        transport=mock_transport(handler),
    )

    assert await adapter.list_node_types(context()) == frozenset({"LoadImage", "SaveImage"})
    with pytest.raises(TypeError):
        await adapter.submit(context(), workflow=API_WORKFLOW, base_url="https://attacker.example")  # type: ignore[call-arg]
    assert [request.method for request in seen] == ["GET"]


async def test_adapter_uses_server_built_api_workflow_and_validates_prompt_identifier() -> None:
    submissions = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submissions
        submissions += 1
        assert request.method == "POST" and request.url.path == "/prompt"
        assert json.loads(request.content) == {"prompt": API_WORKFLOW, "client_id": "idem-1"}
        return httpx.Response(200, json={"prompt_id": "prompt-1"}, request=request)

    adapter = _adapter(
        ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None),
        transport=mock_transport(handler),
    )
    request = ComfyWorkflowRequest("workflow-1", 1, API_WORKFLOW, (), "idem-1")
    job = await adapter.submit(
        context(), request
    )
    repeated = await adapter.submit(
        context(), request
    )

    assert job.service_id == "comfy-local"
    assert job.upstream_job_id == "prompt-1"
    assert job.state.status is JobStatus.QUEUED
    assert repeated == job
    assert submissions == 1


async def test_adapter_does_not_mark_definitive_upstream_rejections_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": "invalid"}, request=request)

    adapter = _adapter(
        ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None),
        transport=mock_transport(handler),
    )

    with pytest.raises(PortalUpstreamError) as raised:
        await adapter.submit(context(), ComfyWorkflowRequest("workflow-1", 1, API_WORKFLOW, (), "idem-1"))

    assert raised.value.retryable is False


async def test_adapter_does_not_replay_an_unknown_submission() -> None:
    submissions = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submissions
        submissions += 1
        return httpx.Response(503, request=request)

    adapter = _adapter(
        ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None),
        transport=mock_transport(handler),
    )
    request = ComfyWorkflowRequest("workflow-1", 1, API_WORKFLOW, (), "idem-1")

    for _ in range(2):
        with pytest.raises(PortalUpstreamError) as raised:
            await adapter.submit(context(), request)
        assert raised.value.code == "SUBMISSION_UNKNOWN"
        assert raised.value.retryable is False

    assert submissions == 1


async def test_cancelled_submit_becomes_unknown_and_is_never_replayed() -> None:
    started = asyncio.Event()
    submissions = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submissions
        submissions += 1
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled request should not complete")

    adapter = _adapter(
        ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None),
        transport=mock_transport(handler),
    )
    request = ComfyWorkflowRequest("workflow-1", 1, API_WORKFLOW, (), "idem-1")
    submission = asyncio.create_task(adapter.submit(context(), request))
    await started.wait()

    submission.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submission
    with pytest.raises(PortalUpstreamError, match="SUBMISSION_UNKNOWN"):
        await asyncio.wait_for(adapter.submit(context(), request), timeout=0.1)

    assert submissions == 1
    assert adapter._in_flight == {}


async def test_same_idempotency_key_is_isolated_per_verified_user() -> None:
    submissions = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submissions
        submissions += 1
        return httpx.Response(200, json={"prompt_id": f"prompt-{submissions}"}, request=request)

    adapter = _adapter(
        ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None),
        transport=mock_transport(handler),
    )
    request = ComfyWorkflowRequest("workflow-1", 1, API_WORKFLOW, (), "idem-1")

    first = await adapter.submit(context("user-1"), request)
    second = await adapter.submit(context("user-2"), request)

    assert (first.upstream_job_id, second.upstream_job_id) == ("prompt-1", "prompt-2")
    assert submissions == 2


async def test_adapter_restores_prompt_ownership_after_restart_before_polling_or_cancelling(tmp_path: Path) -> None:
    store = CanvasStore(tmp_path)
    seen: list[tuple[str, str]] = []

    def submit_handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        assert request.url.path == "/prompt"
        return httpx.Response(200, json={"prompt_id": "prompt-restart"}, request=request)

    declaration = ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None)
    submitted = await ComfyHttpWorkflowService(
        declaration, prompt_owner_store=store, transport=mock_transport(submit_handler)
    ).submit(context("owner-a"), ComfyWorkflowRequest("workflow-1", 1, API_WORKFLOW, (), "idem-restart"))

    def restored_handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == f"/history/{submitted.upstream_job_id}":
            return httpx.Response(200, json={submitted.upstream_job_id: {"status": {"status_str": "running"}}}, request=request)
        if request.url.path == "/queue":
            return httpx.Response(200, json={}, request=request)
        raise AssertionError("unknown or non-owner prompts must not reach upstream")

    restarted = ComfyHttpWorkflowService(
        declaration, prompt_owner_store=store, transport=mock_transport(restored_handler)
    )

    assert (await restarted.poll(context("owner-a"), submitted.upstream_job_id)).status is JobStatus.RUNNING
    await restarted.cancel(context("owner-a"), submitted.upstream_job_id)
    with pytest.raises(PortalUpstreamError) as cross_user:
        await restarted.poll(context("owner-b"), submitted.upstream_job_id)
    with pytest.raises(PortalUpstreamError) as unknown:
        await restarted.cancel(context("owner-a"), "missing-prompt")

    assert cross_user.value.retryable is False
    assert unknown.value.retryable is False
    assert seen == [
        ("POST", "/prompt"),
        ("GET", f"/history/{submitted.upstream_job_id}"),
        ("POST", "/queue"),
    ]


async def test_adapter_marks_an_accepted_submission_unknown_when_owner_persistence_fails() -> None:
    class FailingOwnerStore:
        def record_comfy_prompt_owner(self, **_kwargs) -> bool:
            raise RuntimeError("durable write failed")

        def comfy_prompt_owner(self, service_id: str, prompt_id: str) -> str | None:
            del service_id, prompt_id
            return None

    submissions = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submissions
        submissions += 1
        return httpx.Response(200, json={"prompt_id": "prompt-persist-failure"}, request=request)

    adapter = _adapter(
        ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None),
        prompt_owner_store=FailingOwnerStore(), transport=mock_transport(handler),
    )
    request = ComfyWorkflowRequest("workflow-1", 1, API_WORKFLOW, (), "idem-persist-failure")

    for _ in range(2):
        with pytest.raises(PortalUpstreamError) as raised:
            await adapter.submit(context(), request)
        assert raised.value.code == "SUBMISSION_UNKNOWN"
        assert raised.value.retryable is False

    assert submissions == 1


async def test_prompt_id_collision_revokes_poll_and_cancel_for_all_users_before_upstream_io(tmp_path: Path) -> None:
    store = CanvasStore(tmp_path)
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "prompt-collision"}, request=request)
        raise AssertionError("an ambiguous prompt must not reach upstream")

    declaration = ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None)
    first = ComfyHttpWorkflowService(declaration, prompt_owner_store=store, transport=mock_transport(handler))
    second = ComfyHttpWorkflowService(declaration, prompt_owner_store=store, transport=mock_transport(handler))
    request_a = ComfyWorkflowRequest("workflow-1", 1, API_WORKFLOW, (), "idem-a")
    request_b = ComfyWorkflowRequest("workflow-1", 1, API_WORKFLOW, (), "idem-b")
    job = await first.submit(context("owner-a"), request_a)

    with pytest.raises(PortalUpstreamError) as collision:
        await second.submit(context("owner-b"), request_b)
    assert collision.value.code == "SUBMISSION_UNKNOWN"

    restarted = ComfyHttpWorkflowService(declaration, prompt_owner_store=store, transport=mock_transport(handler))
    for adapter, user_id in ((first, "owner-a"), (second, "owner-b"), (restarted, "owner-a"), (restarted, "owner-b")):
        with pytest.raises(PortalUpstreamError) as poll_error:
            await adapter.poll(context(user_id), job.upstream_job_id)
        with pytest.raises(PortalUpstreamError) as cancel_error:
            await adapter.cancel(context(user_id), job.upstream_job_id)
        assert poll_error.value.retryable is False
        assert cancel_error.value.retryable is False

    assert seen == [("POST", "/prompt"), ("POST", "/prompt")]


async def test_concurrent_submissions_bind_prompt_polling_and_cancellation_to_the_submitter() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/prompt":
            client_id = json.loads(request.content)["client_id"]
            return httpx.Response(200, json={"prompt_id": f"prompt-{client_id}"}, request=request)
        if request.url.path.startswith("/history/"):
            prompt_id = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={prompt_id: {"status": {"status_str": "running"}}}, request=request)
        if request.url.path == "/queue":
            return httpx.Response(200, json={}, request=request)
        raise AssertionError("unexpected upstream request")

    adapter = _adapter(
        ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None),
        transport=mock_transport(handler),
    )
    request_one = ComfyWorkflowRequest("workflow-1", 1, API_WORKFLOW, (), "idem-1")
    request_two = ComfyWorkflowRequest("workflow-1", 1, API_WORKFLOW, (), "idem-2")
    first, second = await asyncio.gather(
        adapter.submit(context("user-1"), request_one),
        adapter.submit(context("user-2"), request_two),
    )

    for owner, other, job in (("user-1", "user-2", first), ("user-2", "user-1", second)):
        with pytest.raises(PortalUpstreamError) as poll_error:
            await adapter.poll(context(other), job.upstream_job_id)
        with pytest.raises(PortalUpstreamError) as cancel_error:
            await adapter.cancel(context(other), job.upstream_job_id)
        assert poll_error.value.retryable is False
        assert cancel_error.value.retryable is False

    with pytest.raises(PortalUpstreamError) as unknown_error:
        await adapter.poll(context("user-1"), "unknown-prompt")

    assert unknown_error.value.retryable is False
    assert seen == [("POST", "/prompt"), ("POST", "/prompt")]
    assert (await adapter.poll(context("user-1"), first.upstream_job_id)).status is JobStatus.RUNNING
    await adapter.cancel(context("user-2"), second.upstream_job_id)
    assert seen == [
        ("POST", "/prompt"), ("POST", "/prompt"),
        ("GET", f"/history/{first.upstream_job_id}"), ("POST", "/queue"),
    ]


async def test_different_user_submissions_do_not_wait_on_another_users_network_request() -> None:
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()
    submissions = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submissions
        submissions += 1
        submission_number = submissions
        if submission_number == 1:
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
        return httpx.Response(200, json={"prompt_id": f"prompt-{submission_number}"}, request=request)

    adapter = _adapter(
        ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None),
        transport=mock_transport(handler),
    )
    request = ComfyWorkflowRequest("workflow-1", 1, API_WORKFLOW, (), "idem-1")
    first = asyncio.create_task(adapter.submit(context("user-1"), request))
    await first_started.wait()
    second = asyncio.create_task(adapter.submit(context("user-2"), request))
    await asyncio.wait_for(second_started.wait(), timeout=0.1)

    release_first.set()
    await first
    await second


async def test_malformed_submission_wakes_same_key_waiters_and_releases_coordination() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        return httpx.Response(200, json={"prompt_id": "not a safe identifier"}, request=request)

    adapter = _adapter(
        ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None),
        transport=mock_transport(handler),
    )
    request = ComfyWorkflowRequest("workflow-1", 1, API_WORKFLOW, (), "idem-1")
    owner = asyncio.create_task(adapter.submit(context(), request))
    await started.wait()
    waiter = asyncio.create_task(adapter.submit(context(), request))

    release.set()
    with pytest.raises(InvalidUpstreamResult):
        await owner
    with pytest.raises(InvalidUpstreamResult):
        await asyncio.wait_for(waiter, timeout=0.1)

    assert adapter._in_flight == {}


async def test_health_is_unavailable_when_the_trusted_service_cannot_be_contacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    adapter = _adapter(
        ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None),
        transport=mock_transport(handler),
    )

    health = await adapter.health(context())

    assert health.service_id == "comfy-local"
    assert health.status is ComfyServiceStatus.UNAVAILABLE
    assert health.node_types == frozenset()


@pytest.mark.parametrize("status_code", (401, 403, 404, 422))
async def test_health_marks_definitive_object_info_rejections_misconfigured(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, request=request)

    adapter = _adapter(
        ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None),
        transport=mock_transport(handler),
    )

    assert (await adapter.health(context())).status is ComfyServiceStatus.MISCONFIGURED


async def test_adapter_resolves_optional_auth_reference_only_on_the_server() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer local-test-token"
        return httpx.Response(200, json={"LoadImage": {}}, request=request)

    declaration = ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, "comfy-local-auth")
    adapter = _adapter(
        declaration,
        auth_header_resolver=lambda reference: {"Authorization": "Bearer local-test-token"},
        transport=mock_transport(handler),
    )

    assert await adapter.list_node_types(context()) == frozenset({"LoadImage"})
    assert "comfy-local-auth" not in repr(declaration)
    assert "local-test-token" not in repr(adapter)


async def test_auth_resolver_failure_is_misconfigured_without_auth_reference() -> None:
    declaration = ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, "comfy-local-auth")
    adapter = _adapter(
        declaration, auth_header_resolver=lambda reference: (_ for _ in ()).throw(RuntimeError(reference))
    )

    health = await adapter.health(context())

    assert health.status is ComfyServiceStatus.MISCONFIGURED
    assert "comfy-local-auth" not in repr(adapter)


def test_app_assembles_comfy_services_only_in_server_state(tmp_path) -> None:
    config_root = tmp_path / "config"
    config_root.mkdir()
    config_path = config_root / "comfyui-services.json"
    config_path.write_text(
        '{"services":[{"service_id":"comfy-local","base_url":"http://127.0.0.1:8188","timeout_seconds":3,"auth_header_ref":"comfy-token"}]}',
        encoding="utf-8",
    )
    app = create_app(
        Settings(
            "test", 8992, tmp_path / "data", "test-secret",
            comfyui_services_config_path=config_path, comfyui_services_config_root=config_root,
        ),
        comfy_auth_header_resolver=lambda reference: {"Authorization": "Bearer local-test-token"},
    )

    assert app.state.comfy_workflow_library is not None
    assert tuple(item.service_id for item in app.state.comfy_workflow_services) == ("comfy-local",)
    assert app.state.adapter_registry.comfy_workflow("comfy-local") is app.state.comfy_workflow_services[0]
    assert app.state.comfy_workflow_services[0]._prompt_owner_store is app.state.canvas_store


def test_app_starts_when_configured_auth_reference_has_no_resolver(tmp_path) -> None:
    config_root = tmp_path / "config"
    config_root.mkdir()
    config_path = config_root / "comfyui-services.json"
    config_path.write_text(
        '{"services":[{"service_id":"comfy-local","base_url":"http://127.0.0.1:8188","timeout_seconds":3,"auth_header_ref":"comfy-token"}]}',
        encoding="utf-8",
    )

    app = create_app(
        Settings(
            "test", 8992, tmp_path / "data", "test-secret",
            comfyui_services_config_path=config_path, comfyui_services_config_root=config_root,
        )
    )

    assert tuple(item.service_id for item in app.state.comfy_workflow_services) == ("comfy-local",)


def test_app_shutdown_closes_registered_comfy_http_clients_without_upstream_requests(tmp_path: Path) -> None:
    config_root = tmp_path / "config"
    config_root.mkdir()
    config_path = config_root / "comfyui-services.json"
    config_path.write_text(
        '{"services":[{"service_id":"comfy-local","base_url":"http://127.0.0.1:8188","timeout_seconds":3,"auth_header_ref":null}]}',
        encoding="utf-8",
    )
    app = create_app(
        Settings(
            "test", 8992, tmp_path / "data", "test-secret",
            comfyui_services_config_path=config_path, comfyui_services_config_root=config_root,
        )
    )
    adapter = app.state.comfy_workflow_services[0]

    assert adapter._client is not None and not adapter._client.is_closed
    with TestClient(app):
        pass
    assert adapter._client.is_closed

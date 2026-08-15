from __future__ import annotations

from collections.abc import Callable
import json

import httpx
import pytest

from ai_creation_canvas.app import create_app
from ai_creation_canvas.comfy.service import (
    ComfyHttpWorkflowService,
    ComfyServiceDeclaration,
    ComfyServiceStatus,
    ComfyWorkflowRequest,
)
from ai_creation_canvas.domain.models import JobStatus, PortalUser, RequestContext
from ai_creation_canvas.config import Settings
from ai_creation_canvas.errors import PortalUpstreamError


API_WORKFLOW = {"1": {"class_type": "LoadImage", "inputs": {}}, "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}}}
pytestmark = pytest.mark.anyio


def context() -> RequestContext:
    return RequestContext(PortalUser("user-1", "Ada", "user"), "request-1", "trace-1")


def mock_transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_adapter_reports_node_inventory_and_never_accepts_callers_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url == httpx.URL("http://127.0.0.1:8188/object_info")
        return httpx.Response(200, json={"LoadImage": {}, "SaveImage": {}}, request=request)

    adapter = ComfyHttpWorkflowService(
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

    adapter = ComfyHttpWorkflowService(
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

    adapter = ComfyHttpWorkflowService(
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

    adapter = ComfyHttpWorkflowService(
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


async def test_health_is_unavailable_when_the_trusted_service_cannot_be_contacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    adapter = ComfyHttpWorkflowService(
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

    adapter = ComfyHttpWorkflowService(
        ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None),
        transport=mock_transport(handler),
    )

    assert (await adapter.health(context())).status is ComfyServiceStatus.MISCONFIGURED


async def test_adapter_resolves_optional_auth_reference_only_on_the_server() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer local-test-token"
        return httpx.Response(200, json={"LoadImage": {}}, request=request)

    declaration = ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, "comfy-local-auth")
    adapter = ComfyHttpWorkflowService(
        declaration,
        auth_header_resolver=lambda reference: {"Authorization": "Bearer local-test-token"},
        transport=mock_transport(handler),
    )

    assert await adapter.list_node_types(context()) == frozenset({"LoadImage"})
    assert "comfy-local-auth" not in repr(declaration)
    assert "local-test-token" not in repr(adapter)


async def test_auth_resolver_failure_is_misconfigured_without_auth_reference() -> None:
    declaration = ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, "comfy-local-auth")
    adapter = ComfyHttpWorkflowService(
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

from __future__ import annotations

import json

import pytest

import httpx

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog, PortalJobsAdapter, ServiceDeclaration
from ai_creation_canvas.adapters.portal.client import PortalClient
from ai_creation_canvas.adapters.portal.identity import AuthRequired
from ai_creation_canvas.domain.models import JobRequest, ModelSpec, PortalUser, RequestContext
from ai_creation_canvas.errors import InvalidUpstreamResult, PortalUpstreamError
from ai_creation_canvas.domain.registry import AdapterRegistry


def context_for() -> RequestContext:
    return RequestContext(PortalUser("u-a", "Alice", "user"), "request-1", "trace-1")


class FakeAdapter:
    def __init__(self, service_id: str, models: tuple[ModelSpec, ...] = (), error: Exception | None = None) -> None:
        self.service_id, self.models, self.error = service_id, models, error

    async def list_models(self, context: RequestContext) -> tuple[ModelSpec, ...]:
        if self.error:
            raise self.error
        return self.models

    async def submit(self, context, request): raise NotImplementedError
    async def poll(self, context, upstream_job_id): raise NotImplementedError


def model(service_id: str, model_id: str) -> ModelSpec:
    return ModelSpec(model_id, service_id, model_id.upper(), ("image.generate",), input_media=("text",))


@pytest.mark.anyio
async def test_catalog_merges_models_deterministically_and_reports_partial_failures():
    registry = AdapterRegistry()
    registry.register_generation(FakeAdapter("video-service", (model("video-service", "c"),)))
    registry.register_generation(FakeAdapter("broken-service", error=RuntimeError("cookie=secret")))
    registry.register_generation(FakeAdapter("image-service", (model("image-service", "b"), model("image-service", "a"))))

    result = await ModelCatalog(registry).list_models(context_for())
    assert [item.model_id for item in result.models] == ["a", "b", "c"]
    assert result.diagnostics == ({"service_id": "broken-service", "code": "MODEL_CATALOG_UNAVAILABLE"},)


@pytest.mark.anyio
async def test_catalog_rejects_duplicate_model_ids_across_services():
    registry = AdapterRegistry()
    registry.register_generation(FakeAdapter("one", (model("one", "same"),)))
    registry.register_generation(FakeAdapter("two", (model("two", "same"),)))
    result = await ModelCatalog(registry).list_models(context_for())
    assert result.models == ()
    assert result.diagnostics == ({"service_id": "one", "code": "DUPLICATE_MODEL_ID"}, {"service_id": "two", "code": "DUPLICATE_MODEL_ID"})


@pytest.mark.anyio
async def test_portal_adapter_ignores_dangerous_unknown_fields_and_rejects_unsupported_operations():
    client = PortalClient(
        "https://portal.test", allowed_mounts=("/image-service",),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json={
            "models": [{
                "id": "image-a", "display_name": "Image A", "operations": ["image.generate"],
                "script": "alert(1)", "upstream_url": "https://example.invalid", "parameter_schema": {},
            }]
        })),
    )
    adapter = PortalJobsAdapter(ServiceDeclaration("image-service", "/image-service", "image", ("image.generate",)), client)
    assert [model.model_id for model in await adapter.list_models(context_for(), cookie_header="current=a")] == ["image-a"]


@pytest.mark.anyio
async def test_portal_adapter_rejects_an_unbounded_parameter_schema():
    nested: dict[str, object] = {"leaf": "value"}
    for _ in range(10):
        nested = {"nested": nested}
    client = PortalClient(
        "https://portal.test", allowed_mounts=("/image-service",),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json={
            "models": [{"id": "image-a", "display_name": "Image A", "operations": ["image.generate"], "parameter_schema": nested}]
        })),
    )
    adapter = PortalJobsAdapter(ServiceDeclaration("image-service", "/image-service", "image", ("image.generate",)), client)
    with pytest.raises(ValueError, match="configuration is invalid"):
        await adapter.list_models(context_for())


@pytest.mark.anyio
@pytest.mark.parametrize("content_type", [None, "text/html", "application/problem+json"])
async def test_portal_adapter_rejects_non_json_content_type_without_parsing_body(content_type):
    headers = {} if content_type is None else {"content-type": content_type}
    client = PortalClient("https://portal.test", allowed_mounts=("/image-service",), transport=httpx.MockTransport(lambda r: httpx.Response(200, headers=headers, content=b'{"models": []}')))
    adapter = PortalJobsAdapter(ServiceDeclaration("image-service", "/image-service", "image", ("image.generate",)), client)
    with pytest.raises(ValueError, match="configuration is invalid"):
        await adapter.list_models(context_for())


@pytest.mark.anyio
async def test_portal_adapter_accepts_case_insensitive_json_content_type():
    client = PortalClient("https://portal.test", allowed_mounts=("/image-service",), transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"content-type": "Application/JSON; charset=utf-8"}, json={"models": []})))
    adapter = PortalJobsAdapter(ServiceDeclaration("image-service", "/image-service", "image", ("image.generate",)), client)
    assert await adapter.list_models(context_for()) == ()


@pytest.mark.anyio
async def test_portal_submission_forwards_the_exact_idempotency_contract():
    seen: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(202, json={"id": "upstream-1", "status": "queued"})

    client = PortalClient("https://portal.test", allowed_mounts=("/image-service",), allowed_methods=("GET", "POST"), transport=httpx.MockTransport(handler))
    adapter = PortalJobsAdapter(ServiceDeclaration("image-service", "/image-service", "image", ("image.generate",)), client)
    await adapter.submit(context_for(), JobRequest("image.generate", "model-a", "paint", "stable-key", {"steps": 4}, ("asset-a",)))
    assert seen == [{"operation": "image.generate", "model_id": "model-a", "prompt": "paint", "params": {"steps": 4}, "asset_ids": ["asset-a"], "idempotency_key": "stable-key"}]


@pytest.mark.anyio
@pytest.mark.parametrize(("status", "retryable"), [(400, False), (429, True), (500, True)])
async def test_portal_submission_maps_http_failures_to_typed_errors(status, retryable):
    client = PortalClient("https://portal.test", allowed_mounts=("/image-service",), allowed_methods=("POST",), transport=httpx.MockTransport(lambda request: httpx.Response(status, text="secret upstream body")))
    adapter = PortalJobsAdapter(ServiceDeclaration("image-service", "/image-service", "image", ("image.generate",)), client)
    with pytest.raises(PortalUpstreamError) as raised:
        await adapter.submit(context_for(), JobRequest("image.generate", "model-a", "paint", "stable-key"))
    assert raised.value.retryable is retryable


@pytest.mark.anyio
@pytest.mark.parametrize("result_ref", [None, "", "signed-result?token=secret"])
async def test_portal_poll_rejects_missing_empty_or_nonopaque_succeeded_result_ids(result_ref):
    client = PortalClient(
        "https://portal.test", allowed_mounts=("/image-service",),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "succeeded", "result_ref": result_ref})),
    )
    adapter = PortalJobsAdapter(ServiceDeclaration("image-service", "/image-service", "image", ("image.generate",)), client)
    with pytest.raises(InvalidUpstreamResult):
        await adapter.poll(context_for(), "upstream-1")


@pytest.mark.anyio
async def test_mixed_local_and_protected_catalog_resolves_local_without_cookie():
    class ProtectedAdapter(FakeAdapter):
        requires_portal_cookie = True
        def __init__(self):
            super().__init__("protected", (model("protected", "protected-model"),))
            self.cookies: list[str] = []
        async def list_models(self, context):
            raise AssertionError("protected catalog must not be loaded without a cookie")
        async def list_models_with_cookie(self, context, cookie_header):
            self.cookies.append(cookie_header)
            return self.models

    registry = AdapterRegistry()
    registry.register_generation(FakeAdapter("local", (model("local", "local-model"),)))
    protected = ProtectedAdapter()
    registry.register_generation(protected)
    catalog = ModelCatalog(registry)
    assert (await catalog.resolve_model(context_for(), "local-model")).service_id == "local"
    assert protected.cookies == []
    with pytest.raises(AuthRequired):
        await catalog.resolve_model(context_for(), "protected-model")
    listed = await catalog.list_models(context_for(), cookie_header="session=a")
    assert [item.model_id for item in listed.models] == ["local-model", "protected-model"]
    assert protected.cookies == ["session=a"]

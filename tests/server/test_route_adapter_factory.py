from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import json
from pathlib import Path

import httpx
import pytest

from ai_creation_canvas.adapters.ark import ArkGenerationAdapter
from ai_creation_canvas.adapters.chiyun import ChiyunGenerationAdapter
from ai_creation_canvas.adapters.factory import ProviderProtocol, RouteAdapterFactory
from ai_creation_canvas.api.admin import _chiyun_template
from ai_creation_canvas.coordination import CredentialLease
from ai_creation_canvas.domain.models import JobRequest, ModelInputPort, ModelOperation, PortalRole, PortalUser, RequestContext
from ai_creation_canvas.model_registry import OperationContract
from ai_creation_canvas.model_routing import ModelRouteDefinition


PNG = b"\x89PNG\r\n\x1a\nroute-result"


def _context() -> RequestContext:
    return RequestContext(PortalUser("user-a", "Alice", PortalRole.USER), "request-a", "trace-a")


def _lease(route_id: str, secret: str = "route-secret-one", pool_id: str = "pool-a") -> CredentialLease:
    return CredentialLease(route_id, pool_id, "key-a", secret, "fingerprint", "owner-token")


def _contract(
    operation: ModelOperation,
    *,
    inputs: tuple[ModelInputPort, ...] | None = None,
    properties: dict[str, object] | None = None,
    mappings: dict[str, str] | None = None,
    required: list[str] | None = None,
) -> OperationContract:
    media = operation.value.split(".", 1)[0]
    return OperationContract(
        operation,
        inputs or (ModelInputPort("prompt", "text", 1, 1),),
        media,
        {
            "type": "object",
            "properties": properties or {},
            **({"required": required} if required is not None else {}),
            "additionalProperties": False,
        },
        mappings or {},
    )


def _route(
    *,
    route_id: str = "route-a",
    provider_id: str = "ark-official",
    provider_model_name: str = "ep-provider-2026",
    adapter_type: str = "ark",
    contract: OperationContract | None = None,
    enabled: bool = True,
    archived_at: str | None = None,
) -> ModelRouteDefinition:
    return ModelRouteDefinition(
        route_id=route_id,
        model_id="logical-model",
        provider_id=provider_id,
        provider_model_name=provider_model_name,
        adapter_type=adapter_type,
        credential_pool_ref="pool-a",
        family="family-a",
        operation_contracts=(contract or _contract(ModelOperation.IMAGE_GENERATE),),
        priority=10,
        max_concurrency=2,
        enabled=enabled,
        archived_at=archived_at,
        revision=3,
    )


def _factory(tmp_path: Path, requests: list[httpx.Request]) -> RouteAdapterFactory:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v3/images/generations":
            return httpx.Response(200, json={"data": [{"url": "https://download.volces.com/result.png"}]})
        if request.url.path == "/api/v3/contents/generations/tasks":
            return httpx.Response(200, json={"id": "cgt-route-task"})
        if request.url.path == "/v1/images/edits":
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
        raise AssertionError(f"unexpected path: {request.url.path}")

    return RouteAdapterFactory(
        data_dir=tmp_path,
        asset_loader=lambda asset_id: ({"ref-one": b"one", "ref-two": b"two"}[asset_id], "image/png"),
        provider_protocols={
            "ark-official": ProviderProtocol("ark-official", "ark", "https://ark.cn-beijing.volces.com"),
            "chiyun": ProviderProtocol("chiyun", "chiyun_openai_images", "https://trusted.chiyun.example"),
        },
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize(
    ("operation", "inputs", "properties", "mappings", "params", "want_path", "want_body"),
    [
        (
            ModelOperation.IMAGE_GENERATE,
            (ModelInputPort("prompt", "text", 1, 1),),
            {"output_count": {"type": "integer", "minimum": 1, "maximum": 15, "default": 1}},
            {"output_count": "n"},
            {"output_count": 2},
            "/api/v3/images/generations",
            {"model": "ep-provider-2026", "prompt": "make it", "n": 2, "response_format": "url"},
        ),
        (
            ModelOperation.IMAGE_EDIT,
            (ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 1, 14)),
            {"watermark": {"type": "boolean", "default": False}},
            {"watermark": "watermark"},
            {"watermark": False},
            "/api/v3/images/generations",
            {"model": "ep-provider-2026", "prompt": "make it", "image": ["data:image/png;base64,b25l"], "watermark": False, "response_format": "url"},
        ),
        (
            ModelOperation.VIDEO_GENERATE,
            (ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 0, 9)),
            {
                "ratio": {"type": "string", "enum": ["adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], "default": "16:9"},
                "duration": {"type": "integer", "minimum": 4, "maximum": 15, "default": 5},
            },
            {"ratio": "ratio", "duration": "duration"},
            {"ratio": "16:9", "duration": 5},
            "/api/v3/contents/generations/tasks",
            {"model": "ep-provider-2026", "content": [{"type": "text", "text": "make it"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,b25l"}, "role": "reference_image"}], "ratio": "16:9", "duration": 5},
        ),
    ],
)
def test_route_factory_builds_exact_ark_operation_template(
    tmp_path: Path,
    operation: ModelOperation,
    inputs: tuple[ModelInputPort, ...],
    properties: dict[str, object],
    mappings: dict[str, str],
    params: dict[str, object],
    want_path: str,
    want_body: dict[str, object],
) -> None:
    requests: list[httpx.Request] = []
    factory = _factory(tmp_path, requests)
    route = _route(contract=_contract(operation, inputs=inputs, properties=properties, mappings=mappings))
    adapter = factory.build(route, _lease(route.route_id))

    async def scenario() -> None:
        request_inputs = {"reference_images": ("ref-one",)} if operation is not ModelOperation.IMAGE_GENERATE else {}
        await adapter.submit(_context(), JobRequest(operation, route.model_id, "make it", "same", params, inputs=request_inputs))

    asyncio.run(scenario())
    assert isinstance(adapter, ArkGenerationAdapter)
    assert adapter.model_ids == ("logical-model",)
    assert len(requests) == 1
    assert requests[0].url == httpx.URL(f"https://ark.cn-beijing.volces.com{want_path}")
    assert json.loads(requests[0].content) == want_body
    assert requests[0].headers["authorization"] == "Bearer route-secret-one"
    assert b"route-secret-one" not in requests[0].content
    assert "route-secret-one" not in str(requests[0].url)


def test_route_factory_builds_exact_chiyun_edit_from_trusted_protocol(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    factory = _factory(tmp_path, requests)
    contract = _contract(
        ModelOperation.IMAGE_EDIT,
        inputs=(ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 1, 10)),
        properties={
            "size": {"type": "string", "enum": ["auto", "1024x1024", "1024x1536", "1536x1024"], "default": "auto"},
            "output_count": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
        },
        mappings={"size": "size", "output_count": "n"},
        required=["size", "output_count"],
    )
    route = _route(provider_id="chiyun", provider_model_name="gpt-image-2", adapter_type="chiyun_openai_images", contract=contract)
    adapter = factory.build(route, _lease(route.route_id))

    asyncio.run(adapter.submit(_context(), JobRequest(
        ModelOperation.IMAGE_EDIT,
        route.model_id,
        "use refs",
        "same",
        {"size": "1024x1024", "output_count": 1},
        inputs={"reference_images": ("ref-two", "ref-one")},
    )))

    assert isinstance(adapter, ChiyunGenerationAdapter)
    assert len(requests) == 1
    request = requests[0]
    assert request.url == httpx.URL("https://trusted.chiyun.example/v1/images/edits")
    assert request.headers["authorization"] == "Bearer route-secret-one"
    assert b"route-secret-one" not in request.content
    assert b'gpt-image-2' in request.content
    assert request.content.index(b"two") < request.content.index(b"one")


def test_route_factory_builds_the_existing_admin_chiyun_template(tmp_path: Path) -> None:
    factory = _factory(tmp_path, [])
    route = _route(
        provider_id="chiyun",
        provider_model_name="gpt-image-2",
        adapter_type="chiyun_openai_images",
        contract=_chiyun_template(),
    )
    assert isinstance(factory.build(route, _lease(route.route_id)), ChiyunGenerationAdapter)


def test_route_factory_rejects_wrong_or_inactive_route_and_untrusted_contracts(tmp_path: Path) -> None:
    factory = _factory(tmp_path, [])
    image = _contract(ModelOperation.IMAGE_GENERATE)
    rejected = [
        _route(adapter_type="chiyun_openai_images", contract=image),
        _route(provider_id="missing", contract=image),
        _route(enabled=False, contract=image),
        _route(enabled=False, archived_at="2026-08-12T00:00:00+00:00", contract=image),
        _route(contract=_contract(ModelOperation.IMAGE_GENERATE, properties={"mode": {"type": "string"}}, mappings={"mode": "shell_command"})),
        _route(contract=_contract(ModelOperation.IMAGE_GENERATE, inputs=(ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 0, 1)))),
        _route(contract=_contract(ModelOperation.VIDEO_GENERATE, inputs=(ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_video", "video", 0, 1)))),
        _route(
            provider_id="chiyun",
            adapter_type="chiyun_openai_images",
            contract=_contract(
                ModelOperation.IMAGE_EDIT,
                inputs=(ModelInputPort("prompt", "image", 1, 1), ModelInputPort("reference_images", "image", 1, 10)),
                properties={
                    "size": {"type": "string", "enum": ["auto", "1024x1024", "1024x1536", "1536x1024"], "default": "auto"},
                    "output_count": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
                },
                mappings={"size": "size", "output_count": "n"},
                required=["size", "output_count"],
            ),
        ),
    ]
    for route in rejected:
        with pytest.raises(ValueError):
            factory.build(route, _lease(route.route_id))

    route = _route()
    with pytest.raises(ValueError):
        factory.build(route, _lease("different-route"))
    with pytest.raises(ValueError):
        factory.build(route, _lease(route.route_id, pool_id="different-pool"))
    with pytest.raises(ValueError):
        factory.build({"route_id": route.route_id, "base_url": "https://evil.example", "headers": {"X-Key": "secret"}}, _lease(route.route_id))  # type: ignore[arg-type]

    purged = replace(route)
    object.__setattr__(purged, "provider_model_name", "")
    with pytest.raises(ValueError):
        factory.build(purged, _lease(route.route_id))


def test_route_factory_never_reuses_an_adapter_or_previous_lease_secret(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    factory = _factory(tmp_path, requests)
    route = _route()
    first = factory.build(route, _lease(route.route_id, "first-route-secret"))
    second = factory.build(route, _lease(route.route_id, "second-route-secret"))

    async def scenario() -> None:
        await first.submit(_context(), JobRequest(ModelOperation.IMAGE_GENERATE, route.model_id, "first prompt", "first"))
        await second.submit(_context(), JobRequest(ModelOperation.IMAGE_GENERATE, route.model_id, "second prompt", "second"))

    asyncio.run(scenario())
    assert first is not second
    assert [request.headers["authorization"] for request in requests] == ["Bearer first-route-secret", "Bearer second-route-secret"]
    assert all(b"route-secret" not in request.content for request in requests)
    assert "first-route-secret" not in repr(factory)
    assert "second-route-secret" not in repr(factory)
    assert "first-route-secret" not in repr(first)
    assert "second-route-secret" not in repr(second)


def test_provider_protocol_registry_is_copied_and_ark_origin_is_fixed(tmp_path: Path) -> None:
    protocols = {"ark-official": ProviderProtocol("ark-official", "ark", "https://ark.cn-beijing.volces.com")}
    factory = RouteAdapterFactory(data_dir=tmp_path, asset_loader=lambda _: (b"one", "image/png"), provider_protocols=protocols)
    protocols.clear()
    assert isinstance(factory.build(_route(), _lease("route-a")), ArkGenerationAdapter)
    with pytest.raises(ValueError):
        ProviderProtocol("ark-official", "ark", "https://evil.example")


def test_route_factory_rejects_parameter_contracts_that_widen_trusted_templates(tmp_path: Path) -> None:
    factory = _factory(tmp_path, [])
    chiyun = _route(
        provider_id="chiyun",
        adapter_type="chiyun_openai_images",
        contract=_contract(
            ModelOperation.IMAGE_EDIT,
            inputs=(ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 1, 10)),
            properties={
                "size": {"type": "string", "enum": ["auto", "1024x1024", "1024x1536", "1536x1024"], "default": "auto"},
                "output_count": {"type": "integer", "minimum": 1, "maximum": 100, "default": 1},
            },
            mappings={"size": "size", "output_count": "n"},
            required=["size", "output_count"],
        ),
    )
    ark_video = _route(contract=_contract(
        ModelOperation.VIDEO_GENERATE,
        properties={"duration": {"type": "integer", "minimum": 1, "maximum": 300, "default": 5}},
        mappings={"duration": "duration"},
    ))
    ark_image = _route(contract=_contract(
        ModelOperation.IMAGE_GENERATE,
        properties={"output_count": {"type": "integer", "minimum": 0, "maximum": 100, "default": 1}},
        mappings={"output_count": "n"},
    ))
    wrong_default = _route(contract=_contract(
        ModelOperation.VIDEO_GENERATE,
        properties={"duration": {"type": "integer", "minimum": 4, "maximum": 30, "default": 6}},
        mappings={"duration": "duration"},
    ))
    widened_enum = _route(contract=_contract(
        ModelOperation.VIDEO_GENERATE,
        properties={"ratio": {"type": "string", "enum": ["16:9", "cinema"], "default": "16:9"}},
        mappings={"ratio": "ratio"},
    ))
    missing_required = _route(
        provider_id="chiyun",
        adapter_type="chiyun_openai_images",
        contract=_contract(
            ModelOperation.IMAGE_EDIT,
            inputs=(ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 1, 10)),
            properties={
                "size": {"type": "string", "enum": ["auto", "1024x1024", "1024x1536", "1536x1024"], "default": "auto"},
                "output_count": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
            },
            mappings={"size": "size", "output_count": "n"},
        ),
    )
    wrong_chiyun_default = _route(
        provider_id="chiyun",
        adapter_type="chiyun_openai_images",
        contract=_contract(
            ModelOperation.IMAGE_EDIT,
            inputs=(ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 1, 10)),
            properties={
                "size": {"type": "string", "enum": ["auto", "1024x1024", "1024x1536", "1536x1024"], "default": "1024x1024"},
                "output_count": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
            },
            mappings={"size": "size", "output_count": "n"},
            required=["size", "output_count"],
        ),
    )
    for route in (chiyun, ark_video, ark_image, wrong_default, widened_enum, missing_required, wrong_chiyun_default):
        with pytest.raises(ValueError, match="parameter"):
            factory.build(route, _lease(route.route_id))


def test_route_factory_accepts_exact_and_strict_parameter_subsets(tmp_path: Path) -> None:
    factory = _factory(tmp_path, [])
    exact = _route(contract=_contract(
        ModelOperation.VIDEO_GENERATE,
        properties={
            "ratio": {"type": "string", "enum": ["adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], "default": "16:9"},
            "duration": {"type": "integer", "minimum": 4, "maximum": 30, "default": 5},
        },
        mappings={"ratio": "ratio", "duration": "duration"},
    ))
    subset = _route(route_id="route-subset", contract=_contract(
        ModelOperation.VIDEO_GENERATE,
        properties={
            "ratio": {"type": "string", "enum": ["16:9", "9:16"], "default": "16:9"},
            "duration": {"type": "integer", "minimum": 5, "maximum": 15, "default": 5},
        },
        mappings={"ratio": "ratio", "duration": "duration"},
    ))
    assert isinstance(factory.build(exact, _lease(exact.route_id)), ArkGenerationAdapter)
    assert isinstance(factory.build(subset, _lease(subset.route_id)), ArkGenerationAdapter)

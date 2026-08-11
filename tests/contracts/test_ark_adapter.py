from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from ai_creation_canvas.domain.models import JobRequest, PortalRole, PortalUser, RequestContext


def context() -> RequestContext:
    return RequestContext(PortalUser("user-a", "Alice", PortalRole.USER), "request-a", "trace-a")


def test_ark_adapter_maps_seedream_image_and_keeps_key_server_side(tmp_path: Path) -> None:
    from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "download.volces.com":
            assert request.headers.get("authorization") is None
            return httpx.Response(200, headers={"content-type": "image/png"}, content=b"safe-image")
        assert request.headers["authorization"] == "Bearer test-only-secret"
        assert request.url.path == "/api/v3/images/generations"
        assert json.loads(request.content) == {
            "model": "image-endpoint",
            "prompt": "a tiny green robot",
            "size": "1024x1024",
            "response_format": "url",
        }
        return httpx.Response(200, json={"data": [{"url": "https://download.volces.com/image.png"}]})

    async def scenario() -> None:
        adapter = ArkGenerationAdapter(
            api_key="test-only-secret",
            data_dir=tmp_path,
            models=(ArkModelDeclaration("image-endpoint", "ark-image", "Seedream", ("image.generate",), {"type": "object", "properties": {"size": {"type": "string", "default": "1024x1024"}}, "additionalProperties": False}, parameter_mappings={"size": "size"}),),
            transport=httpx.MockTransport(handler),
        )
        models = await adapter.list_models(context())
        assert models[0].model_id == "image-endpoint"
        upstream = await adapter.submit(context(), JobRequest("image.generate", "image-endpoint", "a tiny green robot", "idempotent-1", {"size": "1024x1024"}))
        assert upstream.service_id == "ark-image"
        assert upstream.state.status.value == "queued"
        assert upstream.upstream_job_id.startswith("ark_image_")
        assert all("test-only-secret" not in str(item.headers) for item in seen[1:])
        state = await adapter.poll(context(), upstream.upstream_job_id)
        assert state.status.value == "succeeded"
        assert state.result is not None
        stream = await adapter.open_result(context(), state.result.asset_id, cookie_header="", range_header="bytes=0-3")
        assert stream.status_code == 206
        assert b"".join([chunk async for chunk in stream.aiter_bytes()]) == b"safe"

    asyncio.run(scenario())


def test_ark_seedream_preserves_a_bounded_multi_result_response(tmp_path: Path) -> None:
    from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration

    urls = ["https://download.volces.com/one.png", "https://download.volces.com/two.png"]
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": url} for url in urls]})
        return httpx.Response(200, headers={"content-type": "image/png"}, content=request.url.path.encode())

    async def scenario() -> None:
        adapter = ArkGenerationAdapter(
            api_key="test-only-secret", data_dir=tmp_path,
            models=(ArkModelDeclaration("image-endpoint", "ark-image", "Seedream", ("image.generate",), {"type": "object", "properties": {}, "additionalProperties": False}),),
            transport=httpx.MockTransport(handler),
        )
        upstream = await adapter.submit(context(), JobRequest("image.generate", "image-endpoint", "two", "multi"))
        state = await adapter.poll(context(), upstream.upstream_job_id)
        assert state.result is state.results[0]
        assert len(state.results) == 2
        assert len({item.asset_id for item in state.results}) == 2

    asyncio.run(scenario())


def test_ark_adapter_maps_seedance_create_and_poll_without_exposing_result_url(tmp_path: Path) -> None:
    from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/api/v3/contents/generations/tasks"
            assert json.loads(request.content) == {"model": "video-endpoint", "content": [{"type": "text", "text": "clouds drift"}], "ratio": "16:9", "duration": 3}
            return httpx.Response(200, json={"id": "cgt-safe-1"})
        if request.url.host == "download.volces.com":
            assert request.headers.get("authorization") is None
            return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"safe-video")
        assert request.method == "GET"
        assert request.url.path == "/api/v3/contents/generations/tasks/cgt-safe-1"
        return httpx.Response(200, json={"id": "cgt-safe-1", "status": "succeeded", "content": {"video_url": "https://download.volces.com/video.mp4"}})

    async def scenario() -> None:
        adapter = ArkGenerationAdapter(
            api_key="test-only-secret",
            data_dir=tmp_path,
            models=(ArkModelDeclaration("video-endpoint", "ark-video", "Seedance", ("video.generate",), {"type": "object", "properties": {"ratio": {"type": "string", "default": "16:9"}, "duration": {"type": "integer", "default": 3}}, "additionalProperties": False}, parameter_mappings={"ratio": "ratio", "duration": "duration"}),),
            transport=httpx.MockTransport(handler),
        )
        submitted = await adapter.submit(context(), JobRequest("video.generate", "video-endpoint", "clouds drift", "idempotent-2", {"ratio": "16:9", "duration": 3}))
        state = await adapter.poll(context(), submitted.upstream_job_id)
        assert state.status.value == "succeeded"
        assert state.result is not None
        assert state.result.asset_id.startswith("ark_result_")
        assert "download.volces.com" not in state.result.asset_id

    asyncio.run(scenario())


def test_seedance_maps_named_image_roles_and_top_level_parameters_exactly(tmp_path: Path) -> None:
    from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration
    from ai_creation_canvas.domain.models import ModelInputPort

    payloads: list[dict[str, object]] = []
    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content)); return httpx.Response(200, json={"id": "cgt-images"})
    schema = {"type": "object", "properties": {"resolution": {"type": "string"}, "generate_audio": {"type": "boolean"}, "watermark": {"type": "boolean"}}, "additionalProperties": False}

    async def scenario() -> None:
        declaration = ArkModelDeclaration(
            "seedance", "ark-video", "Seedance", ("video.generate",), schema,
            (ModelInputPort("prompt", "text", 1, 1), ModelInputPort("first_frame", "image", 0, 1), ModelInputPort("last_frame", "image", 0, 1), ModelInputPort("reference_images", "image", 0, 9)),
            {"resolution": "resolution", "generate_audio": "generate_audio", "watermark": "watermark"},
        )
        adapter = ArkGenerationAdapter(api_key="test-only-secret", data_dir=tmp_path, models=(declaration,), transport=httpx.MockTransport(handler), asset_loader=lambda asset_id: (asset_id.encode(), "image/png"))
        await adapter.submit(context(), JobRequest("video.generate", "seedance", "animate", "roles", {"resolution": "720p", "generate_audio": False, "watermark": False}, inputs={"first_frame": ("first",), "last_frame": ("last",), "reference_images": ("ref-2", "ref-1")}))

    asyncio.run(scenario())
    assert payloads == [{"model": "seedance", "content": [
        {"type": "text", "text": "animate"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,Zmlyc3Q="}, "role": "first_frame"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,bGFzdA=="}, "role": "last_frame"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,cmVmLTI="}, "role": "reference_image"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,cmVmLTE="}, "role": "reference_image"},
    ], "resolution": "720p", "generate_audio": False, "watermark": False}]


def test_ark_declaration_rejects_unmapped_or_unknown_provider_parameters() -> None:
    from ai_creation_canvas.adapters.ark import ArkModelDeclaration

    schema = {"type": "object", "properties": {"count": {"type": "integer"}}, "additionalProperties": False}
    with pytest.raises(ValueError):
        ArkModelDeclaration("image", "ark-image", "Image", ("image.generate",), schema)
    with pytest.raises(ValueError):
        ArkModelDeclaration("image", "ark-image", "Image", ("image.generate",), schema, parameter_mappings={"count": "shell_command"})


def test_ark_adapter_forwards_every_declared_image_parameter_exactly(tmp_path: Path) -> None:
    from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration

    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"url": "https://download.volces.com/image.png"}]})

    schema = {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "count": {"type": "integer", "minimum": 0},
            "strength": {"type": "number", "minimum": 0},
            "watermark": {"type": "boolean"},
        },
        "additionalProperties": False,
    }

    async def scenario() -> None:
        declaration = ArkModelDeclaration(
            "image-endpoint", "ark-image", "Seedream", ("image.generate",), schema,
            parameter_mappings={"label": "quality", "count": "n", "strength": "strength", "watermark": "watermark"},
        )
        adapter = ArkGenerationAdapter(api_key="test-only-secret", data_dir=tmp_path, models=(declaration,), transport=httpx.MockTransport(handler))
        await adapter.submit(context(), JobRequest("image.generate", "image-endpoint", "prompt", "exact-values", {
            "label": "", "count": 0, "strength": 0.0, "watermark": False,
        }))

    asyncio.run(scenario())
    assert seen == [{
        "model": "image-endpoint", "prompt": "prompt", "quality": "", "n": 0,
        "strength": 0.0, "watermark": False, "response_format": "url",
    }]


def test_seedream_edit_preserves_ordered_reference_images_in_official_payload(tmp_path: Path) -> None:
    from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration
    from ai_creation_canvas.domain.models import ModelInputPort

    payloads: list[dict[str, object]] = []
    assets = {"second": (b"second", "image/png"), "first": (b"first", "image/jpeg")}

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"url": "https://download.volces.com/image.png"}]})

    async def scenario() -> None:
        declaration = ArkModelDeclaration(
            "seedream", "ark-image", "Seedream", ("image.generate", "image.edit"),
            {"type": "object", "properties": {"size": {"type": "string"}}, "additionalProperties": False},
            (ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 0, 14)),
            {"size": "size"},
        )
        adapter = ArkGenerationAdapter(api_key="test-only-secret", data_dir=tmp_path, models=(declaration,), transport=httpx.MockTransport(handler), asset_loader=lambda asset_id: assets[asset_id])
        await adapter.submit(context(), JobRequest("image.edit", "seedream", "combine", "ordered", {"size": "2K"}, inputs={"reference_images": ("second", "first")}))

    asyncio.run(scenario())
    assert payloads == [{
        "model": "seedream", "prompt": "combine", "image": [
            "data:image/png;base64,c2Vjb25k", "data:image/jpeg;base64,Zmlyc3Q=",
        ], "size": "2K", "response_format": "url",
    }]

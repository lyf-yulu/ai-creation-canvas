from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

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
            models=(ArkModelDeclaration("image-endpoint", "ark-image", "Seedream", ("image.generate",), {"type": "object", "properties": {"size": {"type": "string", "default": "1024x1024"}}, "additionalProperties": False}),),
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


def test_ark_adapter_maps_seedance_create_and_poll_without_exposing_result_url(tmp_path: Path) -> None:
    from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/api/v3/contents/generations/tasks"
            assert json.loads(request.content) == {"model": "video-endpoint", "content": [{"type": "text", "text": "clouds drift --ratio 16:9 --dur 3"}]}
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
            models=(ArkModelDeclaration("video-endpoint", "ark-video", "Seedance", ("video.generate",), {"type": "object", "properties": {"ratio": {"type": "string", "default": "16:9"}, "duration": {"type": "integer", "default": 3}}, "additionalProperties": False}),),
            transport=httpx.MockTransport(handler),
        )
        submitted = await adapter.submit(context(), JobRequest("video.generate", "video-endpoint", "clouds drift", "idempotent-2", {"ratio": "16:9", "duration": 3}))
        state = await adapter.poll(context(), submitted.upstream_job_id)
        assert state.status.value == "succeeded"
        assert state.result is not None
        assert state.result.asset_id.startswith("ark_result_")
        assert "download.volces.com" not in state.result.asset_id

    asyncio.run(scenario())

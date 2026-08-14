from __future__ import annotations

import asyncio
import binascii
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import struct
import time
import zlib

import httpx
import pytest

from ai_creation_canvas.domain.models import JobRequest, PortalRole, PortalUser, RequestContext
from ai_creation_canvas.errors import PortalUpstreamError


def context() -> RequestContext:
    return RequestContext(PortalUser("user-a", "Alice", PortalRole.USER), "request-a", "trace-a")


def png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\x00\xff\x55" * width
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(row * height, 9)) + chunk(b"IEND", b"")


def test_ark_cancel_uses_official_delete_without_a_body(tmp_path: Path) -> None:
    from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    async def scenario() -> None:
        adapter = ArkGenerationAdapter(
            api_key="test-only-secret", data_dir=tmp_path,
            models=(ArkModelDeclaration("video-endpoint", "ark-video", "Seedance", ("video.generate",), {"type": "object", "properties": {}}),),
            transport=httpx.MockTransport(handler),
        )
        await adapter.cancel(context(), "cgt-safe_123")
        assert len(seen) == 1
        assert seen[0].method == "DELETE"
        assert seen[0].url.path == "/api/v3/contents/generations/tasks/cgt-safe_123"
        assert seen[0].content == b""
        with pytest.raises(ValueError):
            await adapter.cancel(context(), "ark_image_unsafe")
        assert len(seen) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("status,retryable", [(429, True), (500, True), (400, False), (404, False)])
def test_ark_cancel_maps_provider_failures_safely(tmp_path: Path, status: int, retryable: bool) -> None:
    from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration

    async def scenario() -> None:
        adapter = ArkGenerationAdapter(
            api_key="test-only-secret", data_dir=tmp_path,
            models=(ArkModelDeclaration("video-endpoint", "ark-video", "Seedance", ("video.generate",), {"type": "object", "properties": {}}),),
            transport=httpx.MockTransport(lambda request: httpx.Response(status)),
        )
        with pytest.raises(PortalUpstreamError) as caught:
            await adapter.cancel(context(), "cgt-safe")
        assert caught.value.retryable is retryable
        assert caught.value.status_code == status

    asyncio.run(scenario())


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
        assert "n" not in json.loads(request.content)
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
        pending = json.loads((tmp_path / "ark-results" / "pending.json").read_text(encoding="utf-8"))
        assert upstream.upstream_job_id in pending
        await adapter.acknowledge_poll_result(upstream.upstream_job_id)
        pending = json.loads((tmp_path / "ark-results" / "pending.json").read_text(encoding="utf-8"))
        assert upstream.upstream_job_id not in pending
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
        replayed = await adapter.poll(context(), upstream.upstream_job_id)
        assert state.result is state.results[0]
        assert len(state.results) == 2
        assert len({item.asset_id for item in state.results}) == 2
        assert tuple(item.asset_id for item in replayed.results) == tuple(item.asset_id for item in state.results)
        pending = json.loads((tmp_path / "ark-results" / "pending.json").read_text(encoding="utf-8"))
        assert upstream.upstream_job_id in pending
        await adapter.acknowledge_poll_result(upstream.upstream_job_id)
        pending = json.loads((tmp_path / "ark-results" / "pending.json").read_text(encoding="utf-8"))
        assert upstream.upstream_job_id not in pending

    asyncio.run(scenario())


def test_ark_pending_updates_are_safe_across_adapter_instances(tmp_path: Path) -> None:
    from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration

    declaration = ArkModelDeclaration(
        "image-endpoint", "ark-image", "Seedream", ("image.generate",),
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": [{"url": "https://download.volces.com/image.png"}]})
    )
    first = ArkGenerationAdapter(
        api_key="test-only-secret", data_dir=tmp_path, models=(declaration,), transport=transport,
    )
    second = ArkGenerationAdapter(
        api_key="test-only-secret", data_dir=tmp_path, models=(declaration,), transport=transport,
    )
    job_a = asyncio.run(first.submit(
        context(), JobRequest("image.generate", "image-endpoint", "a", "job-a")
    ))
    original_read = first._read_index

    def slow_read():
        values = original_read()
        time.sleep(0.1)
        return values

    first._read_index = slow_read
    with ThreadPoolExecutor(max_workers=2) as pool:
        acknowledgement = pool.submit(
            lambda: asyncio.run(first.acknowledge_poll_result(job_a.upstream_job_id))
        )
        time.sleep(0.02)
        submitted = pool.submit(
            lambda: asyncio.run(second.submit(
                context(), JobRequest("image.generate", "image-endpoint", "b", "job-b")
            ))
        ).result()
        acknowledgement.result()

    pending = json.loads((tmp_path / "ark-results" / "pending.json").read_text(encoding="utf-8"))
    assert submitted.upstream_job_id in pending


def test_ark_seedream_accepts_the_official_fifteen_image_group_bound(tmp_path: Path) -> None:
    from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration

    urls = [f"https://download.volces.com/{index}.png" for index in range(15)]

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
        submitted = await adapter.submit(context(), JobRequest("image.generate", "image-endpoint", "series", "fifteen"))
        state = await adapter.poll(context(), submitted.upstream_job_id)
        assert len(state.results) == 15
        assert len({item.asset_id for item in state.results}) == 15

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
        adapter = ArkGenerationAdapter(api_key="test-only-secret", data_dir=tmp_path, models=(declaration,), transport=httpx.MockTransport(handler), asset_loader=lambda asset_id: (png(640, 640), "image/png"))
        await adapter.submit(context(), JobRequest("video.generate", "seedance", "animate", "roles", {"resolution": "720p", "generate_audio": False, "watermark": False}, inputs={"first_frame": ("first",), "last_frame": ("last",), "reference_images": ("ref-2", "ref-1")}))

    asyncio.run(scenario())
    assert payloads == [{"model": "seedance", "content": [
        {"type": "text", "text": "animate"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + __import__("base64").b64encode(png(640, 640)).decode()}, "role": "first_frame"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + __import__("base64").b64encode(png(640, 640)).decode()}, "role": "last_frame"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + __import__("base64").b64encode(png(640, 640)).decode()}, "role": "reference_image"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + __import__("base64").b64encode(png(640, 640)).decode()}, "role": "reference_image"},
    ], "resolution": "720p", "generate_audio": False, "watermark": False}]


def test_seedance_rejects_images_below_the_official_300px_floor_before_post(tmp_path: Path) -> None:
    from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration
    from ai_creation_canvas.domain.models import ModelInputPort

    requests: list[httpx.Request] = []
    declaration = ArkModelDeclaration(
        "seedance", "ark-video", "Seedance", ("video.generate",),
        {"type": "object", "properties": {}, "additionalProperties": False},
        (ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 0, 30)), {},
    )

    async def scenario() -> None:
        adapter = ArkGenerationAdapter(
            api_key="test-only-secret", data_dir=tmp_path, models=(declaration,),
            transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200, json={"id": "cgt-unsafe"})),
            asset_loader=lambda _asset_id: (png(64, 64), "image/png"),
        )
        with pytest.raises(ValueError, match="Submission request is invalid"):
            await adapter.submit(context(), JobRequest("video.generate", "seedance", "animate", "too-small", inputs={"reference_images": ("small",)}))

    asyncio.run(scenario())
    assert requests == []


def test_seedance_maps_owned_audio_as_bounded_data_url_and_keeps_video_fail_closed(tmp_path: Path) -> None:
    from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration
    from ai_creation_canvas.domain.models import ModelInputPort

    payloads: list[dict[str, object]] = []
    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content)); return httpx.Response(200, json={"id": "cgt-audio"})
    declaration = ArkModelDeclaration(
        "seedance", "ark-video", "Seedance", ("video.generate",), {"type": "object", "properties": {}, "additionalProperties": False},
        (ModelInputPort("prompt", "text", 1, 1), ModelInputPort("first_frame", "image", 0, 1), ModelInputPort("reference_audio", "audio", 0, 3)), {},
    )

    async def scenario() -> None:
        adapter = ArkGenerationAdapter(api_key="test-only-secret", data_dir=tmp_path, models=(declaration,), transport=httpx.MockTransport(handler), asset_loader=lambda asset_id: ((b"RIFFaudioWAVE" if asset_id == "audio" else png(640, 640)), "audio/wav" if asset_id == "audio" else "image/png"))
        await adapter.submit(context(), JobRequest("video.generate", "seedance", "speak", "audio", inputs={"first_frame": ("image",), "reference_audio": ("audio",)}))
        with pytest.raises(ValueError, match="audio inputs are invalid"):
            await adapter.submit(context(), JobRequest("video.generate", "seedance", "audio only", "audio-only", inputs={"reference_audio": ("audio",)}))
        with pytest.raises(ValueError, match="unsupported asset flow"):
            await adapter.submit(context(), JobRequest("video.generate", "seedance", "move", "video", inputs={"reference_video": ("video",)}))

    asyncio.run(scenario())
    assert payloads == [{"model": "seedance", "content": [
        {"type": "text", "text": "speak"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + __import__("base64").b64encode(png(640, 640)).decode()}, "role": "first_frame"},
        {"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64,UklGRmF1ZGlvV0FWRQ=="}, "role": "reference_audio"},
    ]}]


def test_ark_declaration_rejects_unmapped_or_unknown_provider_parameters() -> None:
    from ai_creation_canvas.adapters.ark import ArkModelDeclaration

    schema = {"type": "object", "properties": {"count": {"type": "integer"}}, "additionalProperties": False}
    with pytest.raises(ValueError):
        ArkModelDeclaration("image", "ark-image", "Image", ("image.generate",), schema)
    with pytest.raises(ValueError):
        ArkModelDeclaration("image", "ark-image", "Image", ("image.generate",), schema, parameter_mappings={"count": "shell_command"})

    duplicate_schema = {"type": "object", "properties": {"width": {"type": "integer"}, "height": {"type": "integer"}}, "additionalProperties": False}
    with pytest.raises(ValueError, match="unique"):
        ArkModelDeclaration("image", "ark-image", "Image", ("image.generate",), duplicate_schema, parameter_mappings={"width": "size", "height": "size"})


def test_ark_adapter_compiles_declared_nested_image_parameters(tmp_path: Path) -> None:
    from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration

    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"url": "https://download.volces.com/image.png"}]})

    schema = {
        "type": "object",
        "properties": {
            "size": {
                "type": "string",
                "default": "2K",
                "x-ark-size": {"presets": ["1K", "1.5K", "2K"], "min_pixels": 921600, "max_pixels": 4624220, "min_ratio": 0.0625, "max_ratio": 16},
            },
            "prompt_optimization": {"type": "string", "enum": ["standard", "fast"], "default": "standard"},
            "sequence_mode": {"type": "string", "enum": ["disabled", "auto"], "default": "disabled"},
            "max_images": {"type": "integer", "minimum": 1, "maximum": 15, "default": 4},
            "output_format": {"type": "string", "enum": ["png", "jpeg"], "default": "png"},
            "watermark": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    }

    async def scenario() -> None:
        declaration = ArkModelDeclaration(
            "seedream", "ark-image", "Seedream", ("image.generate",), schema,
            parameter_mappings={
                "size": "size",
                "prompt_optimization": "optimize_prompt_options.mode",
                "sequence_mode": "sequential_image_generation",
                "max_images": "sequential_image_generation_options.max_images",
                "output_format": "output_format",
                "watermark": "watermark",
            },
        )
        adapter = ArkGenerationAdapter(api_key="test-only-secret", data_dir=tmp_path, models=(declaration,), transport=httpx.MockTransport(handler))
        await adapter.submit(context(), JobRequest("image.generate", "seedream", "four seasons", "nested", {
            "size": "2048x1024", "prompt_optimization": "fast", "sequence_mode": "auto", "max_images": 4,
            "output_format": "png", "watermark": False,
        }))
        with pytest.raises(ValueError, match="parameters"):
            await adapter.submit(context(), JobRequest("image.generate", "seedream", "bad size", "bad-small", {
                "size": "512x512", "prompt_optimization": "fast", "sequence_mode": "disabled", "max_images": 4,
                "output_format": "png", "watermark": False,
            }))
        with pytest.raises(ValueError, match="parameters"):
            await adapter.submit(context(), JobRequest("image.generate", "seedream", "bad ratio", "bad-ratio", {
                "size": "4000x100", "prompt_optimization": "fast", "sequence_mode": "disabled", "max_images": 4,
                "output_format": "png", "watermark": False,
            }))

    asyncio.run(scenario())
    assert payloads == [{
        "model": "seedream",
        "prompt": "four seasons",
        "size": "2048x1024",
        "optimize_prompt_options": {"mode": "fast"},
        "sequential_image_generation": "auto",
        "sequential_image_generation_options": {"max_images": 4},
        "output_format": "png",
        "watermark": False,
        "response_format": "url",
    }]


@pytest.mark.parametrize(
    "target",
    ["__proto__.polluted", "constructor.prototype", "optimize_prompt_options.mode.extra", "unknown.mode"],
)
def test_ark_declaration_rejects_unsafe_nested_parameter_targets(target: str) -> None:
    from ai_creation_canvas.adapters.ark import ArkModelDeclaration

    schema = {"type": "object", "properties": {"mode": {"type": "string"}}, "additionalProperties": False}
    with pytest.raises(ValueError, match="mapping"):
        ArkModelDeclaration("image", "ark-image", "Image", ("image.generate",), schema, parameter_mappings={"mode": target})


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

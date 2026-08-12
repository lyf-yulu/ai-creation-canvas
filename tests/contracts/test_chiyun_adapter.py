from __future__ import annotations

import base64
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from ai_creation_canvas.adapters.chiyun import ChiyunGenerationAdapter
from ai_creation_canvas.domain.models import JobRequest, ModelInputPort, ModelOperation, PortalRole, PortalUser, RequestContext
from ai_creation_canvas.errors import InvalidUpstreamResult, PortalUpstreamError
from ai_creation_canvas.model_registry import GovernedModelDefinition, ModelModality, OperationContract, ProviderDefinition


PNG = b"\x89PNG\r\n\x1a\n" + b"safe-image"
JPEG = b"\xff\xd8\xff" + b"safe-jpeg"


def context() -> RequestContext:
    return RequestContext(PortalUser("user-a", "Alice", PortalRole.USER), "request-a", "trace-a")


def provider() -> ProviderDefinition:
    return ProviderDefinition("chiyun", "Chiyun", "chiyun_openai_images", "https://chiyun.example", "chiyun-primary")


def model() -> GovernedModelDefinition:
    return GovernedModelDefinition(
        "chiyun-gpt-image-2", "chiyun", "gpt-image-2", "GPT Image 2", "Chiyun 多参考图编辑", ModelModality.IMAGE,
        (OperationContract(
            ModelOperation.IMAGE_EDIT,
            (ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 1, 10)),
            "image",
            {"type": "object", "properties": {"size": {"type": "string", "enum": ["auto", "1024x1024", "1024x1536", "1536x1024"]}, "output_count": {"type": "integer", "minimum": 1, "maximum": 4}}, "required": ["size", "output_count"], "additionalProperties": False},
            {"size": "size", "output_count": "n"},
        ),),
    )


def fields(body: bytes, name: str) -> list[bytes]:
    marker = f'name="{name}"'.encode()
    values: list[bytes] = []
    for section in body.split(b"--"):
        if marker in section and b"\r\n\r\n" in section:
            values.append(section.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0])
    return values


def test_chiyun_submits_ordered_multipart_and_materializes_bounded_results(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    assets = {"one": (PNG, "image/png"), "two": (JPEG, "image/jpeg")}
    adapter = ChiyunGenerationAdapter(
        provider=provider(), models=(model(),), api_key="test-only-secret", data_dir=tmp_path,
        asset_loader=lambda asset_id: assets[asset_id], transport=httpx.MockTransport(handler),
    )
    async def scenario():
        upstream = await adapter.submit(context(), JobRequest(
            "image.edit", "chiyun-gpt-image-2", "preserve @图片1 then @图片2", "same-key",
            {"size": "1024x1536", "output_count": 1}, inputs={"reference_images": ("one", "two")},
        ))
        state = await adapter.poll(context(), upstream.upstream_job_id)
        assert state.status.value == "succeeded"
        assert state.result is not None and state.result.mime_type == "image/png"
        stream = await adapter.open_result(context(), state.result.asset_id, cookie_header="", range_header="bytes=0-7")
        assert stream.status_code == 206
        assert b"".join([chunk async for chunk in stream.aiter_bytes()]) == PNG[:8]
        return upstream
    upstream = asyncio.run(scenario())

    assert upstream.state.status.value == "queued"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/images/edits"
    assert request.headers["authorization"] == "Bearer test-only-secret"
    assert fields(request.content, "model") == [b"gpt-image-2"]
    assert fields(request.content, "prompt") == ["preserve @图片1 then @图片2".encode()]
    assert fields(request.content, "size") == [b"1024x1536"]
    assert fields(request.content, "n") == [b"1"]
    assert fields(request.content, "image[]") == [PNG, JPEG]

@pytest.mark.parametrize(
    "job_request",
    [
        JobRequest("image.generate", "chiyun-gpt-image-2", "x", "key", {"size": "auto", "output_count": 1}, inputs={"reference_images": ("one",)}),
        JobRequest("image.edit", "chiyun-gpt-image-2", "x", "key", {"size": "auto", "output_count": 1}),
        JobRequest("image.edit", "chiyun-gpt-image-2", "x", "key", {"size": "auto", "output_count": 1}, inputs={"reference_images": tuple(str(i) for i in range(11))}),
    ],
)
def test_chiyun_rejects_operations_or_inputs_outside_the_model_contract(tmp_path: Path, job_request: JobRequest) -> None:
    calls = 0
    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)
    adapter = ChiyunGenerationAdapter(provider=provider(), models=(model(),), api_key="test-only-secret", data_dir=tmp_path, asset_loader=lambda _: (PNG, "image/png"), transport=httpx.MockTransport(handler))
    async def scenario() -> None:
        with pytest.raises(ValueError):
            await adapter.submit(context(), job_request)
    asyncio.run(scenario())
    assert calls == 0


@pytest.mark.parametrize("status,retryable", [(401, False), (400, False), (429, True), (503, True)])
def test_chiyun_classifies_errors_without_leaking_response(tmp_path: Path, status: int, retryable: bool) -> None:
    adapter = ChiyunGenerationAdapter(
        provider=provider(), models=(model(),), api_key="test-only-secret", data_dir=tmp_path,
        asset_loader=lambda _: (PNG, "image/png"),
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text="raw-secret-error")),
    )
    async def scenario():
        with pytest.raises(PortalUpstreamError) as caught:
            await adapter.submit(context(), JobRequest("image.edit", "chiyun-gpt-image-2", "x", "key", {"size": "auto", "output_count": 1}, inputs={"reference_images": ("one",)}))
        return caught.value
    error = asyncio.run(scenario())
    assert error.retryable is retryable
    assert "raw-secret-error" not in str(error)


@pytest.mark.parametrize("body", [b"not-json", b"[]", json.dumps({"data": [{"b64_json": "!!!"}]}).encode(), json.dumps({"data": [{"url": "http://127.0.0.1/private"}]}).encode()])
def test_chiyun_rejects_invalid_or_unsafe_results(tmp_path: Path, body: bytes) -> None:
    adapter = ChiyunGenerationAdapter(
        provider=provider(), models=(model(),), api_key="test-only-secret", data_dir=tmp_path,
        asset_loader=lambda _: (PNG, "image/png"), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)),
    )
    async def scenario() -> None:
        with pytest.raises(InvalidUpstreamResult):
            await adapter.submit(context(), JobRequest("image.edit", "chiyun-gpt-image-2", "x", "key", {"size": "auto", "output_count": 1}, inputs={"reference_images": ("one",)}))
    asyncio.run(scenario())

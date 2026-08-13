from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import httpx

from ai_creation_canvas.adapters.chiyun_gemini import ChiyunGeminiGenerationAdapter
from ai_creation_canvas.domain.models import JobRequest, ModelInputPort, ModelOperation, PortalRole, PortalUser, RequestContext
from ai_creation_canvas.model_registry import GovernedModelDefinition, ModelModality, OperationContract, ProviderDefinition


PNG = b"\x89PNG\r\n\x1a\n" + b"safe-image"
JPEG = b"\xff\xd8\xff" + b"safe-jpeg"


def context() -> RequestContext:
    return RequestContext(PortalUser("user-a", "Alice", PortalRole.USER), "request-a", "trace-a")


def provider() -> ProviderDefinition:
    return ProviderDefinition("chiyun-banana", "Chiyun Banana", "chiyun_gemini_images", "https://chiyun.work", "banana-key")


def model() -> GovernedModelDefinition:
    return GovernedModelDefinition(
        "banana", "chiyun-banana", "banana2-ssvip", "Banana", "Gemini image edit", ModelModality.IMAGE,
        (OperationContract(
            ModelOperation.IMAGE_EDIT,
            (ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 1, 10)),
            "image",
            {"type": "object", "properties": {
                "aspect_ratio": {"type": "string", "enum": ["1:1", "16:9", "9:16", "4:3", "3:4"], "default": "1:1"},
                "image_size": {"type": "string", "enum": ["1K", "2K", "4K"], "default": "2K"},
            }, "required": ["aspect_ratio", "image_size"], "additionalProperties": False},
            {"aspect_ratio": "aspectRatio", "image_size": "imageSize"},
        ),),
    )


def test_gemini_submits_ordered_inline_images_and_materializes_result(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": {
            "mimeType": "image/png", "data": base64.b64encode(PNG).decode(),
        }}]}}]})

    assets = {"one": (PNG, "image/png"), "two": (JPEG, "image/jpeg")}
    adapter = ChiyunGeminiGenerationAdapter(
        provider=provider(), models=(model(),), api_key="test-only-secret", data_dir=tmp_path,
        asset_loader=lambda asset_id: assets[asset_id], transport=httpx.MockTransport(handler),
    )

    async def scenario() -> None:
        upstream = await adapter.submit(context(), JobRequest(
            "image.edit", "banana", "keep @图片1 then @图片2", "same-key",
            {"aspect_ratio": "1:1", "image_size": "2K"}, inputs={"reference_images": ("one", "two")},
        ))
        state = await adapter.poll(context(), upstream.upstream_job_id)
        assert state.status.value == "succeeded"
        assert state.result is not None and state.result.mime_type == "image/png"
        stream = await adapter.open_result(context(), state.result.asset_id, cookie_header="", range_header="bytes=0-7")
        assert stream.status_code == 206
        assert b"".join([chunk async for chunk in stream.aiter_bytes()]) == PNG[:8]

    asyncio.run(scenario())
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1beta/models/banana2-ssvip:generateContent"
    assert request.headers["authorization"] == "Bearer test-only-secret"
    body = __import__("json").loads(request.content)
    parts = body["contents"][0]["parts"]
    assert parts[0] == {"text": "keep @图片1 then @图片2"}
    assert [part["inline_data"]["mime_type"] for part in parts[1:]] == ["image/png", "image/jpeg"]
    assert [base64.b64decode(part["inline_data"]["data"]) for part in parts[1:]] == [PNG, JPEG]
    assert body["generationConfig"]["imageConfig"] == {"aspectRatio": "1:1", "imageSize": "2K"}

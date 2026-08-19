from __future__ import annotations

import hashlib
import asyncio

import httpx

from ai_creation_canvas.adapters.demo import DemoGenerationAdapter
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import JobRequest, JobStatus, PortalRole, PortalUser, RequestContext
from ai_creation_canvas.errors import AdapterNotFoundError


EXPECTED_PNG_SHA256 = "75ca10aac6917604cd81b3f2090af5fdba577350209abb1781cd53e76a3f9d33"


def context(user_id: str = "user-a") -> RequestContext:
    return RequestContext(PortalUser(user_id, user_id, PortalRole.USER), "request-1", "trace-1")


def request(key: str = "same-key") -> JobRequest:
    return JobRequest("image.generate", "demo-image-v1", "offline prompt", key, {"aspect_ratio": "landscape"})


async def stream_bytes(stream) -> bytes:
    return b"".join([chunk async for chunk in stream.aiter_bytes()])


def test_demo_adapter_is_offline_idempotent_and_range_capable(monkeypatch) -> None:
    async def network_forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("the offline demo adapter attempted a network request")

    monkeypatch.setattr(httpx.AsyncClient, "send", network_forbidden)
    async def scenario() -> None:
        adapter = DemoGenerationAdapter()
        models = await adapter.list_models(context())
        assert models[0].model_id == "demo-image-v1"
        assert models[0].display_name == "本地演示图片"
        assert models[0].parameter_schema["properties"]["aspect_ratio"]["enum"] == ("square", "portrait", "landscape")
        declared = {port.port_id: port for port in models[0].input_ports}
        assert declared["prompt"].media_type == "text"
        assert (declared["prompt"].min_items, declared["prompt"].max_items) == (1, 1)
        assert declared["reference_images"].media_type == "image"
        assert declared["reference_images"].min_items == 0

        first = await adapter.submit(context(), request())
        second = await adapter.submit(context(), request())
        assert first.upstream_job_id == second.upstream_job_id
        state = await adapter.poll(context(), first.upstream_job_id)
        assert state.status is JobStatus.SUCCEEDED
        assert state.result is not None

        full = await adapter.open_result(context(), state.result.asset_id, cookie_header="", head=False)
        payload = await stream_bytes(full)
        assert hashlib.sha256(payload).hexdigest() == EXPECTED_PNG_SHA256
        assert full.status_code == 200
        assert full.headers["content-type"] == "image/png"

        ranged = await adapter.open_result(context(), state.result.asset_id, cookie_header="", range_header="bytes=0-9", head=False)
        assert ranged.status_code == 206
        assert ranged.headers["content-length"] == "10"
        assert ranged.headers["content-range"].startswith("bytes 0-9/")
        assert len(await stream_bytes(ranged)) == 10

        head = await adapter.open_result(context(), state.result.asset_id, cookie_header="", head=True)
        assert head.status_code == 200
        assert await stream_bytes(head) == b""
    asyncio.run(scenario())


def test_demo_adapter_rejects_unknown_opaque_result() -> None:
    async def scenario() -> None:
        stream = await DemoGenerationAdapter().open_result(context(), "not-a-demo-result", cookie_header="", head=False)
        assert stream.status_code == 404
        await stream.aclose()
    asyncio.run(scenario())


def test_demo_registration_requires_both_local_identity_and_explicit_flag(tmp_path) -> None:
    default_local = create_app(Settings("test", 8992, tmp_path / "default", "test-secret", identity_mode="local", allowed_origins=("http://127.0.0.1:8992",)))
    signed_with_flag = create_app(Settings("test", 8993, tmp_path / "signed", "test-secret", enable_demo_adapter=True))

    for app in (default_local, signed_with_flag):
        try:
            app.state.adapter_registry.generation("demo-image")
        except AdapterNotFoundError:
            pass
        else:
            raise AssertionError("demo adapter was enabled outside explicit local mode")

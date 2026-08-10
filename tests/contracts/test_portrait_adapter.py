from __future__ import annotations

import json
import asyncio
import os
import threading

import httpx
import pytest

from ai_creation_canvas.adapters.portal.client import PortalClient
from ai_creation_canvas.adapters.portal.portrait import PortalPortraitAdapter, PortraitDeclaration
from ai_creation_canvas.domain.models import AssetRef, JobRequest, PortalUser, RequestContext
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.errors import InvalidUpstreamResult


def context():
    return RequestContext(PortalUser("user-a", "A", "user"), "request", "trace")


@pytest.mark.anyio
async def test_portrait_adapter_registers_both_capabilities_and_uses_cookie_per_request():
    seen = []
    def handler(request):
        seen.append((request.url.path, request.headers.get("cookie"), request.content))
        if request.url.path.endswith("/api/config"):
            return httpx.Response(200, headers={"content-type": "application/json"}, json={"models": [{"id": "portrait-video", "display_name": "Portrait video", "operations": ["video.image_to_video"], "input_media": ["text", "image"]}]})
        if request.url.path.endswith("/api/virtual/groups"):
            return httpx.Response(201, json={"id": "group-A"})
        if request.url.path.endswith("/api/virtual/assets"):
            return httpx.Response(201, json={"id": "upstream-A", "status": "Processing", "mime_type": "image/png"})
        raise AssertionError(request.url.path)
    client = PortalClient("https://portal.test", allowed_mounts=("/portrait",), allowed_methods=("GET", "POST"), transport=httpx.MockTransport(handler))
    adapter = PortalPortraitAdapter(PortraitDeclaration("portal-portrait", "/portrait"), client)
    registry = AdapterRegistry(); registry.register_asset(adapter); registry.register_generation(adapter)
    model = (await adapter.list_models_with_cookie(context(), "session=a"))[0]
    source = __import__("pathlib").Path("/tmp/portrait-adapter-test.png"); source.write_bytes(b"png")
    uploaded = await adapter.upload_with_cookie(context(), AssetRef("local", "portrait", "processing", "image/png"), source, 3, "session=a")
    assert model.requires_asset_kind == "portrait"
    assert model.operations == ("video.image_to_video",)
    assert uploaded.asset_id == "upstream-A" and uploaded.status == "processing"
    assert [item[0] for item in seen] == ["/portrait/api/config", "/portrait/api/virtual/groups", "/portrait/api/virtual/assets"]
    assert all(item[1] == "session=a" for item in seen)
    assert b"realperson" not in seen[-1][2] and b"virtual-person" not in seen[-1][2]


@pytest.mark.anyio
async def test_portrait_adapter_rejects_unsafe_opaque_ids_and_unknown_status():
    def handler(request):
        return httpx.Response(200, json={"id": "bad/id", "status": "Mystery", "mime_type": "image/png"})
    client = PortalClient("https://portal.test", allowed_mounts=("/portrait",), allowed_methods=("GET", "POST"), transport=httpx.MockTransport(handler))
    adapter = PortalPortraitAdapter(PortraitDeclaration("portal-portrait", "/portrait"), client)
    with pytest.raises(InvalidUpstreamResult):
        await adapter.get_with_cookie(context(), "bad/id", "session=a")


@pytest.mark.anyio
async def test_fd_read_waits_for_worker_despite_multiple_cancellations(monkeypatch, tmp_path):
    started, release = threading.Event(), threading.Event()
    original = os.read
    def blocked(fd, amount):
        started.set(); release.wait(1); return original(fd, amount)
    monkeypatch.setattr(os, "read", blocked)
    source = tmp_path / "portrait-read.tmp"; source.write_bytes(b"x")
    fd = os.open(source, os.O_RDONLY)
    task = asyncio.create_task(PortalPortraitAdapter._read_fd_safely(fd, 1))
    await asyncio.to_thread(started.wait, 1)
    task.cancel(); task.cancel(); task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError): await task
    os.close(fd)

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
from ai_creation_canvas.errors import InvalidUpstreamResult, PortalUpstreamError


def context():
    return RequestContext(PortalUser("user-a", "A", "user"), "request", "trace")


@pytest.mark.anyio
async def test_portrait_adapter_registers_both_capabilities_and_uses_cookie_per_request(tmp_path):
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
    source = tmp_path / "portrait-adapter-test.png"; source.write_bytes(b"png")
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


@pytest.mark.anyio
@pytest.mark.parametrize("worker_error", [False, True])
async def test_cancelled_upload_keeps_its_fd_open_until_the_blocked_reader_exits(monkeypatch, tmp_path, worker_error):
    """The real upload path must not close an FD while a cancelled thread owns it."""
    started, release = threading.Event(), threading.Event()
    original_read, captured = os.read, []

    def blocked_read(fd, amount):
        captured.append(fd)
        started.set()
        release.wait(2)
        if worker_error:
            raise OSError("reader failed after cancellation")
        return original_read(fd, amount)

    def handler(request):
        if request.url.path.endswith("/groups"):
            return httpx.Response(201, headers={"content-type": "application/json"}, json={"id": "group-A"})
        if request.url.path.endswith("/assets"):
            return httpx.Response(201, headers={"content-type": "application/json"}, json={"id": "upstream-A", "status": "Processing", "mime_type": "image/png"})
        raise AssertionError(request.url.path)

    monkeypatch.setattr(os, "read", blocked_read)
    source = tmp_path / "portrait.png"; source.write_bytes(b"x")
    client = PortalClient("https://portal.test", allowed_mounts=("/portrait",), allowed_methods=("POST",), transport=httpx.MockTransport(handler))
    adapter = PortalPortraitAdapter(PortraitDeclaration("portal-portrait", "/portrait"), client)
    task = asyncio.create_task(adapter.upload_with_cookie(context(), AssetRef("local", "portrait", "processing", "image/png"), source, 1, "session=a"))
    await asyncio.to_thread(started.wait, 1)
    assert captured
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done(), "the cancellation cleanup must wait for the read worker"
    task.cancel(); task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    os.fstat(captured[0])
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(OSError):
        os.fstat(captured[0])


def _multipart_parts(body: bytes, boundary: str) -> list[bytes]:
    pieces = body.split(f"--{boundary}".encode())
    assert pieces[0] == b"" and pieces[-1] == b"--\r\n"
    return [piece[2:-2] for piece in pieces[1:-1]]


@pytest.mark.anyio
async def test_upload_uses_a_fresh_exact_multipart_contract_without_boundary_injection(tmp_path):
    seen: list[httpx.Request] = []
    source = tmp_path / "portrait.png"
    source.write_bytes(b"ordinary payload\r\n--not-the-real-boundary--\r\n")

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/groups"):
            return httpx.Response(201, headers={"content-type": "application/json"}, json={"id": "group-A"})
        return httpx.Response(201, headers={"content-type": "application/json"}, json={"id": "upstream-A", "status": "Processing", "mime_type": "image/png"})

    client = PortalClient("https://portal.test", allowed_mounts=("/portrait",), allowed_methods=("POST",), transport=httpx.MockTransport(handler))
    adapter = PortalPortraitAdapter(PortraitDeclaration("portal-portrait", "/portrait"), client)
    asset = AssetRef("local", "portrait", "processing", "image/png")
    await adapter.upload_with_cookie(context(), asset, source, source.stat().st_size, "session=a")
    await adapter.upload_with_cookie(context(), asset, source, source.stat().st_size, "session=a")
    requests = [request for request in seen if request.url.path.endswith("/assets")]
    assert len(requests) == 2
    boundaries = []
    for request in requests:
        content_type = request.headers["content-type"]
        assert content_type.startswith("multipart/form-data; boundary=")
        boundary = content_type.rsplit("=", 1)[1]
        boundaries.append(boundary)
        assert int(request.headers["content-length"]) == len(request.content)
        parts = _multipart_parts(request.content, boundary)
        assert len(parts) == 2
        assert parts[0] == b'Content-Disposition: form-data; name="group_id"\r\n\r\ngroup-A'
        assert parts[1] == b'Content-Disposition: form-data; name="file"; filename="upload.bin"\r\nContent-Type: image/png\r\n\r\n' + source.read_bytes()
    assert boundaries[0] != boundaries[1]


@pytest.mark.anyio
@pytest.mark.parametrize("mime", ["image/png\r\nX-Injected: yes", "text/plain"])
async def test_upload_rejects_invalid_mime_before_any_request(tmp_path, mime):
    source = tmp_path / "portrait.bin"; source.write_bytes(b"x")
    client = PortalClient("https://portal.test", allowed_mounts=("/portrait",), allowed_methods=("POST",), transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError("must not request upstream"))))
    adapter = PortalPortraitAdapter(PortraitDeclaration("portal-portrait", "/portrait"), client)
    with pytest.raises(ValueError, match="portrait upload is invalid"):
        await adapter.upload_with_cookie(context(), AssetRef("local", "portrait", "processing", mime), source, 1, "session=a")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "response", "method", "expected"),
    [
        ("groups", httpx.Response(201, content=b"not-json", headers={"content-type": "application/json"}), "upload", InvalidUpstreamResult),
        ("groups", httpx.Response(201, json={}, headers={"content-type": "application/json"}), "upload", InvalidUpstreamResult),
        ("assets/asset-A", httpx.Response(200, json=["not-object"], headers={"content-type": "application/json"}), "get", InvalidUpstreamResult),
        ("assets/asset-A", httpx.Response(200, json={"id": "asset-A", "status": "Active"}, headers={"content-type": "text/html"}), "get", InvalidUpstreamResult),
        ("jobs", httpx.Response(202, json={"status": "queued"}, headers={"content-type": "application/json"}), "submit", InvalidUpstreamResult),
        ("jobs", httpx.Response(202, json={"id": "job-A", "status": "mystery"}, headers={"content-type": "application/json"}), "submit", InvalidUpstreamResult),
        ("jobs/job-A", httpx.Response(200, json={"id": "job-A"}, headers={"content-type": "text/plain"}), "poll", InvalidUpstreamResult),
    ],
)
async def test_each_portrait_upstream_business_path_rejects_invalid_success_protocol(tmp_path, path, response, method, expected):
    source = tmp_path / "portrait.png"; source.write_bytes(b"x")
    def handler(request):
        if request.url.path.endswith("/groups"):
            return response if path == "groups" else httpx.Response(201, headers={"content-type": "application/json"}, json={"id": "group-A"})
        if request.url.path.endswith("/assets"):
            return response if path == "assets" else httpx.Response(201, headers={"content-type": "application/json"}, json={"id": "asset-A", "status": "Processing", "mime_type": "image/png"})
        if request.url.path.endswith("/assets/asset-A") or request.url.path.endswith("/jobs/job-A") or request.url.path.endswith("/jobs"):
            return response
        raise AssertionError(request.url.path)
    client = PortalClient("https://portal.test", allowed_mounts=("/portrait",), allowed_methods=("GET", "POST"), transport=httpx.MockTransport(handler))
    adapter = PortalPortraitAdapter(PortraitDeclaration("portal-portrait", "/portrait"), client)
    with pytest.raises(expected):
        if method == "upload":
            await adapter.upload_with_cookie(context(), AssetRef("local", "portrait", "processing", "image/png"), source, 1, "session=a")
        elif method == "get":
            await adapter.get_with_cookie(context(), "asset-A", "session=a")
        elif method == "submit":
            await adapter.submit_with_cookie(context(), JobRequest("video.image_to_video", "portrait-video", "wave", "key", {}, ("asset-A",)), "session=a")
        else:
            await adapter.poll_with_cookie(context(), "job-A", "session=a")


@pytest.mark.anyio
async def test_upload_maps_a_transport_failure_to_a_retryable_upstream_error(tmp_path):
    source = tmp_path / "portrait.png"; source.write_bytes(b"x")
    def handler(request):
        raise httpx.ReadTimeout("lost", request=request)
    client = PortalClient("https://portal.test", allowed_mounts=("/portrait",), allowed_methods=("POST",), transport=httpx.MockTransport(handler))
    adapter = PortalPortraitAdapter(PortraitDeclaration("portal-portrait", "/portrait"), client)
    with pytest.raises(PortalUpstreamError) as raised:
        await adapter.upload_with_cookie(context(), AssetRef("local", "portrait", "processing", "image/png"), source, 1, "session=a")
    assert raised.value.retryable is True

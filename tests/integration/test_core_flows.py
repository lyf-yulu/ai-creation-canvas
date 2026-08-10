"""End-to-end public API checks using only in-process Portal service mocks.

The fixture deliberately uses real Canvas routing, SQLite storage and Portal
adapters.  It never opens a connection to a configured Portal or model port.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog, PortalJobsAdapter, ServiceDeclaration
from ai_creation_canvas.adapters.portal.client import PortalClient
from ai_creation_canvas.adapters.portal.portrait import PortalPortraitAdapter, PortraitDeclaration
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import AssetRef, JobState
from ai_creation_canvas.domain.registry import AdapterRegistry


ROOT = Path(__file__).resolve().parents[2]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND\xaeB`\x82"


def signed_headers(user_id: str, *, cookie: bool = True) -> dict[str, str]:
    timestamp = str(int(time.time()))
    username = "Alice" if user_id == "user-a" else "Bob"
    payload = f"v2\n{timestamp}\n{user_id}\nuser\n{quote(username, safe='')}"
    signature = hmac.new(b"integration-secret", payload.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-Portal-Sig-Version": "2",
        "X-Portal-Timestamp": timestamp,
        "X-Portal-User-Id": user_id,
        "X-Portal-Username": username,
        "X-Portal-Role": "user",
        "X-Portal-Signature": signature,
    }
    if cookie:
        headers["Cookie"] = f"portal_session={user_id}"
    return headers


class ResultPortraitAdapter(PortalPortraitAdapter):
    """Test service contract adds the same protected result stream as jobs services."""

    async def poll_with_cookie(self, context, upstream_job_id, cookie_header):
        await super().poll_with_cookie(context, upstream_job_id, cookie_header)
        return JobState(upstream_job_id, "succeeded", result=AssetRef(f"result-{upstream_job_id}", "reference", "active", "video/mp4"))

    async def open_result(self, context, result_id, *, cookie_header, range_header=None, head=False):
        headers = {"Range": range_header} if range_header else None
        return await self._client.open_stream(
            context, "HEAD" if head else "GET", f"api/results/{result_id}",
            mount=self._declaration.mount, cookie_header=cookie_header, headers=headers,
        )


@pytest.fixture
def canvas(tmp_path):
    usage: list[tuple[str, str]] = []
    jobs: dict[str, tuple[str, str]] = {}
    sequence = 0

    def portal(request: httpx.Request) -> httpx.Response:
        nonlocal sequence
        path = request.url.path
        mount, _, resource = path.lstrip("/").partition("/")
        cookie = request.headers.get("cookie", "")
        user = cookie.partition("=")[2]
        if request.method == "GET" and resource == "api/config":
            models = {
                "images": [
                    {"id": "image-model", "display_name": "Image", "operations": ["image.generate", "image.edit"], "input_media": ["text", "image"], "parameter_schema": {}},
                ],
                "videos": [
                    {"id": "video-model", "display_name": "Video", "operations": ["video.generate", "video.image_to_video"], "input_media": ["text", "image"], "parameter_schema": {}},
                ],
                "portrait": [
                    {"id": "portrait-model", "display_name": "Portrait", "operations": ["video.image_to_video"], "input_media": ["text", "image"], "parameter_schema": {}},
                ],
            }
            return httpx.Response(200, json={"models": models[mount]})
        if mount == "portrait" and request.method == "POST" and resource == "api/virtual/groups":
            return httpx.Response(201, json={"id": "portrait-group"})
        if mount == "portrait" and request.method == "POST" and resource == "api/virtual/assets":
            return httpx.Response(202, json={"id": "portrait-upstream", "status": "Processing", "mime_type": "image/png"})
        if mount == "portrait" and request.method == "GET" and resource == "api/virtual/assets/portrait-upstream":
            return httpx.Response(200, json={"id": "portrait-upstream", "status": "Active", "mime_type": "image/png"})
        if request.method == "POST" and resource in {"api/jobs", "api/virtual/jobs"}:
            sequence += 1
            upstream_id = f"job-{sequence}"
            jobs[upstream_id] = (mount, user)
            usage.append((user, mount))
            return httpx.Response(201, json={"id": upstream_id, "status": "queued"})
        if request.method == "GET" and resource.startswith("api/jobs/"):
            upstream_id = resource.rsplit("/", 1)[1]
            assert jobs[upstream_id][0] == mount
            return httpx.Response(200, json={"id": upstream_id, "status": "succeeded", "result_ref": f"result-{upstream_id}"})
        if request.method == "GET" and resource.startswith("api/virtual/jobs/"):
            upstream_id = resource.rsplit("/", 1)[1]
            assert jobs[upstream_id][0] == mount
            return httpx.Response(200, json={"id": upstream_id, "status": "queued"})
        if request.method in {"GET", "HEAD"} and resource.startswith("api/results/"):
            result_id = resource.rsplit("/", 1)[1]
            data = b"mock-video" if jobs[result_id.removeprefix("result-")][0] != "images" else PNG
            mime = "video/mp4" if data == b"mock-video" else "image/png"
            return httpx.Response(200, stream=httpx.ByteStream(data if request.method == "GET" else b""), headers={"content-type": mime, "content-length": str(len(data))})
        raise AssertionError(f"unexpected in-process Portal request: {request.method} {path}")

    transport = httpx.MockTransport(portal)
    client = PortalClient("http://127.0.0.1:45679", allowed_mounts=("/images", "/videos", "/portrait"), allowed_methods=("GET", "POST", "HEAD"), allow_loopback_http=True, transport=transport)
    registry = AdapterRegistry()
    registry.register_generation(PortalJobsAdapter(ServiceDeclaration("images", "/images", "image", ("image.generate", "image.edit")), client))
    registry.register_generation(PortalJobsAdapter(ServiceDeclaration("videos", "/videos", "video", ("video.generate", "video.image_to_video")), client))
    portrait = ResultPortraitAdapter(PortraitDeclaration("portal-portrait", "/portrait"), client)
    registry.register_asset(portrait)
    registry.register_generation(portrait)
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("canvas", encoding="utf-8")
    app = create_app(Settings("test", 45680, tmp_path / "canvas-data", "integration-secret"), static_dir=static_dir, registry=registry, model_catalog=ModelCatalog(registry))
    return TestClient(app, raise_server_exceptions=False), usage


def _upload(client: TestClient, user: str, *, kind: str = "reference") -> str:
    response = client.post("/api/v1/assets", files={"file": ("reference.png", PNG, "image/png")}, data={"kind": kind}, headers=signed_headers(user))
    assert response.status_code == 201, response.text
    asset_id = response.json()["asset_id"]
    if kind == "portrait":
        activated = client.get(f"/api/v1/assets/{asset_id}", headers=signed_headers(user))
        assert activated.status_code == 200 and activated.json()["status"] == "active", activated.text
    return asset_id


@pytest.mark.parametrize(
    ("operation", "model_id", "asset_kind"),
    [
        ("image.generate", "image-model", None),
        ("image.edit", "image-model", "reference"),
        ("video.generate", "video-model", None),
        ("video.image_to_video", "video-model", "reference"),
        ("video.image_to_video", "portrait-model", "portrait"),
    ],
)
def test_public_api_core_flow_polls_result_records_one_usage_and_isolates_users(canvas, operation, model_id, asset_kind):
    client, usage = canvas
    asset_ids = [_upload(client, "user-a", kind=asset_kind)] if asset_kind else []
    response = client.post("/api/v1/jobs", json={"operation": operation, "model_id": model_id, "prompt": "integration prompt", "params": {}, "asset_ids": asset_ids, "idempotency_key": f"{operation}-{model_id}"}, headers=signed_headers("user-a"))
    assert response.status_code == 201, response.text
    job_id = response.json()["id"]
    assert client.get(f"/api/v1/jobs/{job_id}", headers=signed_headers("user-b")).status_code == 403
    assert client.get(f"/api/v1/results/{job_id}", headers=signed_headers("user-b")).status_code == 403
    if asset_ids:
        assert client.get(f"/api/v1/assets/{asset_ids[0]}", headers=signed_headers("user-b")).status_code == 403
    completed = client.get(f"/api/v1/jobs/{job_id}", headers=signed_headers("user-a"))
    assert completed.status_code == 200 and completed.json()["status"] == "succeeded"
    result = client.get(f"/api/v1/results/{job_id}", headers=signed_headers("user-a"))
    assert result.status_code == 200 and result.content
    assert len(usage) == 1 and usage[0][0] == "user-a"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_release_build_is_node_free_at_runtime_and_excludes_sensitive_files(tmp_path):
    script = ROOT / "scripts" / "build-release.sh"
    release = tmp_path / "release"
    completed = subprocess.run(["bash", str(script), str(release)], cwd=tmp_path, text=True, capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert (release / "manifest.sha256").is_file()
    forbidden = ("node_modules", ".git", ".env", "state", "outputs", "uploads", "logs", "archives", ".sqlite", ".db", ".map")
    assert not any(any(token in str(path.relative_to(release)) for token in forbidden) for path in release.rglob("*"))
    restricted_path = "/usr/bin:/bin"
    assert shutil.which("node", path=restricted_path) is None and shutil.which("bun", path=restricted_path) is None
    port = _free_port()
    environment = {"PATH": restricted_path, "PYTHONPATH": str(release / "server"), "PORT": str(port)}
    command = "from pathlib import Path; import os, uvicorn; from ai_creation_canvas.app import create_app; from ai_creation_canvas.config import Settings; uvicorn.run(create_app(Settings('test', int(os.environ['PORT']), Path('runtime-data'), 'release-test-secret')), host='127.0.0.1', port=int(os.environ['PORT']), log_level='error')"
    process = subprocess.Popen([sys.executable, "-c", command], cwd=release, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(50):
            try:
                root = httpx.get(f"http://127.0.0.1:{port}/", headers={"accept": "text/html"}, timeout=0.2)
                break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            raise AssertionError("staged Python server did not start")
        assert root.status_code == 200
        assert httpx.get(f"http://127.0.0.1:{port}/projects/example", headers={"accept": "text/html"}).status_code == 200
        assert httpx.get(f"http://127.0.0.1:{port}/api/v1/session").status_code == 401
    finally:
        process.terminate()
        process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
        shutil.rmtree(release / "runtime-data", ignore_errors=True)
    assert not (release / "runtime-data").exists()

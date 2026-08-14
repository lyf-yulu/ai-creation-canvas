"""End-to-end public API checks using only in-process Portal service mocks.

The fixture deliberately uses real Canvas routing, SQLite storage and Portal
adapters.  It never opens a connection to a configured Portal or model port.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
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
from ai_creation_canvas.domain.models import JobState, ModelSpec, PortalUser, RequestContext, UpstreamJob
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.errors import InvalidUpstreamResult


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


@pytest.fixture
def canvas(tmp_path):
    usage: list[tuple[str, str]] = []
    jobs: dict[str, tuple[str, str]] = {}
    provider_calls: list[tuple[str, str]] = []
    sequence = 0

    def portal(request: httpx.Request) -> httpx.Response:
        nonlocal sequence
        path = request.url.path
        provider_calls.append((request.method, path))
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
            return httpx.Response(200, json={"id": upstream_id, "status": "succeeded", "result_ref": f"result-{upstream_id}"})
        if request.method in {"GET", "HEAD"} and (resource.startswith("api/results/") or resource.startswith("api/virtual/results/")):
            result_id = resource.rsplit("/", 1)[1]
            data = b"mock-video" if jobs[result_id.removeprefix("result-")][0] != "images" else PNG
            mime = "video/mp4" if data == b"mock-video" else "image/png"
            if request.headers.get("range") == "bytes=0-3":
                return httpx.Response(206, stream=httpx.ByteStream(data[:4]), headers={"content-type": mime, "content-length": "4", "content-range": f"bytes 0-3/{len(data)}", "accept-ranges": "bytes"})
            return httpx.Response(200, stream=httpx.ByteStream(data if request.method == "GET" else b""), headers={"content-type": mime, "content-length": str(len(data)), "accept-ranges": "bytes"})
        raise AssertionError(f"unexpected in-process Portal request: {request.method} {path}")

    transport = httpx.MockTransport(portal)
    client = PortalClient("http://127.0.0.1:45679", allowed_mounts=("/images", "/videos", "/portrait"), allowed_methods=("GET", "POST", "HEAD"), allow_loopback_http=True, transport=transport)
    registry = AdapterRegistry()
    images = PortalJobsAdapter(ServiceDeclaration("images", "/images", "image", ("image.generate", "image.edit")), client)
    videos = PortalJobsAdapter(ServiceDeclaration("videos", "/videos", "video", ("video.generate", "video.image_to_video")), client)
    portrait = PortalPortraitAdapter(PortraitDeclaration("portal-portrait", "/portrait"), client)
    for adapter in (images, videos, portrait):
        registry.register_generation(adapter)
    registry.register_asset(portrait)
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("canvas", encoding="utf-8")
    app = create_app(Settings("test", 45680, tmp_path / "canvas-data", "integration-secret"), static_dir=static_dir, registry=registry, model_catalog=ModelCatalog(registry))
    return TestClient(app, raise_server_exceptions=False), usage, provider_calls


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
    client, usage, provider_calls = canvas
    asset_ids = [_upload(client, "user-a", kind=asset_kind)] if asset_kind else []
    response = client.post("/api/v1/jobs", json={"operation": operation, "model_id": model_id, "prompt": "integration prompt", "params": {}, "asset_ids": asset_ids, "idempotency_key": f"{operation}-{model_id}"}, headers=signed_headers("user-a"))
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "queued"
    job_id = response.json()["id"]
    calls_before_cross_user = len(provider_calls)
    assert client.get(f"/api/v1/jobs/{job_id}", headers=signed_headers("user-b")).status_code == 404
    assert len(provider_calls) == calls_before_cross_user
    assert client.get(f"/api/v1/results/{job_id}", headers=signed_headers("user-b")).status_code == 404
    if asset_ids:
        assert client.get(f"/api/v1/assets/{asset_ids[0]}", headers=signed_headers("user-b")).status_code == 403
    assert asyncio.run(client.app.state.job_worker.run_once()) is False
    stored_before, _ = client.app.state.canvas_store.job_for_owner(job_id, "user-a")
    assert stored_before is not None and stored_before["completion_mode"] == "request"
    missing_cookie = client.get(
        f"/api/v1/jobs/{job_id}", headers=signed_headers("user-a", cookie=False)
    )
    assert missing_cookie.status_code == 401
    assert missing_cookie.json()["code"] == "AUTH_REQUIRED"
    stored_after_missing, _ = client.app.state.canvas_store.job_for_owner(job_id, "user-a")
    assert stored_after_missing == stored_before
    provider_calls_before_get = len(provider_calls)
    completed = client.get(f"/api/v1/jobs/{job_id}", headers=signed_headers("user-a"))
    assert completed.status_code == 200 and completed.json()["status"] == "succeeded"
    assert len(provider_calls) == provider_calls_before_get + 1
    assert b"portal_session=user-a" not in client.app.state.canvas_store.database.read_bytes()
    result = client.get(f"/api/v1/results/{job_id}", headers=signed_headers("user-a"))
    assert result.status_code == 200 and result.content
    if model_id == "portrait-model":
        assert client.head(f"/api/v1/results/{job_id}", headers=signed_headers("user-a")).status_code == 200
        ranged = client.get(f"/api/v1/results/{job_id}", headers={**signed_headers("user-a"), "range": "bytes=0-3"})
        assert ranged.status_code == 206 and ranged.content == b"mock"
    assert len(usage) == 1 and usage[0][0] == "user-a"


def test_cookie_portal_async_route_is_accepted_as_request_scoped(tmp_path):
    posts = 0

    def portal(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.method == "GET" and request.url.path == "/images/api/config":
            return httpx.Response(200, json={"models": [{
                "id": "image-model",
                "display_name": "Image",
                "operations": ["image.generate"],
                "input_media": ["text"],
                "parameter_schema": {},
            }]})
        if request.method == "POST":
            posts += 1
            return httpx.Response(201, json={"id": "provider-job", "status": "queued"})
        raise AssertionError("unexpected Portal request")

    portal_client = PortalClient(
        "http://127.0.0.1:45679",
        allowed_mounts=("/images",),
        allowed_methods=("GET", "POST"),
        allow_loopback_http=True,
        transport=httpx.MockTransport(portal),
    )
    registry = AdapterRegistry()
    registry.register_generation(PortalJobsAdapter(
        ServiceDeclaration("images", "/images", "image", ("image.generate",)),
        portal_client,
    ))
    app = create_app(
        Settings("test", 45680, tmp_path / "data", "integration-secret"),
        registry=registry,
        model_catalog=ModelCatalog(registry),
    )

    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/jobs",
        headers=signed_headers("user-a"),
        json={
            "operation": "image.generate",
            "model_id": "image-model",
            "prompt": "integration prompt",
            "params": {},
            "asset_ids": [],
            "idempotency_key": "unsupported-cookie-route",
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "queued"
    assert posts == 1
    stored, forbidden = app.state.canvas_store.job_for_owner(response.json()["id"], "user-a")
    assert stored is not None and forbidden is False
    assert stored["completion_mode"] == "request"


def test_spoofed_request_scoped_capability_is_rejected_before_submission(tmp_path):
    class SpoofedAdapter:
        service_id = "spoofed"
        requires_portal_cookie = True
        requires_request_scoped_polling = True

        def __init__(self):
            self.submits = 0

        async def list_models(self, context):
            return (ModelSpec(
                "spoofed-model", self.service_id, "Spoofed", ("image.generate",), ("text",), {}
            ),)

        async def submit(self, context, request):
            self.submits += 1
            return UpstreamJob(
                self.service_id, "spoofed-upstream", JobState("spoofed-upstream", "queued")
            )

        async def poll(self, context, upstream_job_id):
            raise AssertionError("spoofed adapter must not be polled")

        async def submit_with_cookie(self, context, request, cookie_header):
            return await self.submit(context, request)

        async def poll_with_cookie(self, context, upstream_job_id, cookie_header):
            raise AssertionError("spoofed adapter must not receive Cookie polling")

    adapter = SpoofedAdapter()
    registry = AdapterRegistry()
    registry.register_generation(adapter)
    app = create_app(
        Settings("test", 45680, tmp_path / "data", "integration-secret"),
        registry=registry,
        model_catalog=ModelCatalog(registry),
    )

    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/jobs",
        headers=signed_headers("user-a"),
        json={
            "operation": "image.generate",
            "model_id": "spoofed-model",
            "prompt": "must not submit",
            "params": {},
            "asset_ids": [],
            "idempotency_key": "spoofed-capability",
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "MODEL_UNAVAILABLE"
    assert adapter.submits == 0


def test_production_portrait_adapter_rejects_external_or_nonopaque_result_references():
    def invalid_result(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/portrait/api/virtual/jobs/job-1"
        return httpx.Response(200, json={"id": "job-1", "status": "succeeded", "result_ref": "https://outside.invalid/result"})

    adapter = PortalPortraitAdapter(
        PortraitDeclaration("portal-portrait", "/portrait"),
        PortalClient("http://127.0.0.1:45679", allowed_mounts=("/portrait",), allowed_methods=("GET", "POST", "HEAD"), allow_loopback_http=True, transport=httpx.MockTransport(invalid_result)),
    )
    context = RequestContext(PortalUser("user-a", "Alice", "user"), "request", "trace")
    import asyncio
    with pytest.raises(InvalidUpstreamResult):
        asyncio.run(adapter.poll_with_cookie(context, "job-1", "portal_session=user-a"))
    with pytest.raises(InvalidUpstreamResult):
        asyncio.run(adapter.open_result(context, "https://outside.invalid/result", cookie_header="portal_session=user-a"))


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
    manifest_check = subprocess.run(
        ["shasum", "-a", "256", "-c", "manifest.sha256"],
        cwd=release,
        text=True,
        capture_output=True,
        check=False,
    )
    assert manifest_check.returncode == 0, manifest_check.stderr
    assert ".ai-creation-canvas-release-marker" not in (release / "manifest.sha256").read_text(encoding="utf-8")
    services = release / "server" / "config" / "services.example.json"
    assert services.is_file()
    checked = subprocess.run(
        [sys.executable, "-m", "ai_creation_canvas", "--environment", "test", "--port", str(_free_port()), "--data-dir", str(release / "check-data"), "--portal-internal-token", "release-test-secret", "--portal-base-url", "http://127.0.0.1:45679", "--services-config", str(services), "--allow-loopback-http", "--check-config"],
        cwd=release,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(release / "server")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    assert set(checked.stdout.split()) == {"example-images", "example-videos", "portal-portrait"}
    shutil.rmtree(release / "check-data", ignore_errors=True)
    stamp = ROOT / "web" / "dist" / ".ai-creation-canvas-build-input.sha256"
    assert stamp.is_file()
    original_stamp = stamp.read_text(encoding="utf-8")
    cached_release = tmp_path / "cached-release"
    cached = subprocess.run(["bash", str(script), "--skip-web-build", str(cached_release)], cwd=tmp_path, text=True, capture_output=True, check=False)
    assert cached.returncode == 0, cached.stderr
    release = cached_release
    services = release / "server" / "config" / "services.example.json"
    try:
        stamp.write_text("tampered\n", encoding="utf-8")
        stale = subprocess.run(["bash", str(script), "--skip-web-build", str(tmp_path / "stale-release")], cwd=tmp_path, text=True, capture_output=True, check=False)
        assert stale.returncode != 0
        assert not (tmp_path / "stale-release").exists()
    finally:
        stamp.write_text(original_stamp, encoding="utf-8")
    forbidden = ("node_modules", ".git", ".env", "state", "outputs", "uploads", "logs", "archives", ".sqlite", ".db", ".map")
    assert not any(any(token in str(path.relative_to(release)) for token in forbidden) for path in release.rglob("*"))
    restricted_path = "/usr/bin:/bin"
    assert shutil.which("node", path=restricted_path) is None and shutil.which("bun", path=restricted_path) is None
    port = _free_port()
    environment = {"PATH": restricted_path, "PYTHONPATH": str(release / "server")}
    command = [sys.executable, "-m", "ai_creation_canvas", "--environment", "test", "--port", str(port), "--data-dir", "runtime-data", "--portal-internal-token", "release-test-secret", "--portal-base-url", "http://127.0.0.1:45679", "--services-config", str(services), "--allow-loopback-http"]
    process = subprocess.Popen(command, cwd=release, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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


def test_release_build_failure_cleans_only_its_new_target(tmp_path):
    script = ROOT / "scripts" / "build-release.sh"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_npm = fake_bin / "npm"
    fake_npm.write_text("#!/usr/bin/env bash\nexit 73\n", encoding="utf-8")
    fake_npm.chmod(0o755)
    environment = {**dict(__import__("os").environ), "PATH": f"{fake_bin}:{Path(sys.executable).parent}:/usr/bin:/bin"}
    failed_target = tmp_path / "failed-release"
    failed = subprocess.run(["bash", str(script), str(failed_target)], cwd=tmp_path, env=environment, text=True, capture_output=True, check=False)
    assert failed.returncode != 0 and not failed_target.exists()
    existing = tmp_path / "existing-release"
    existing.mkdir()
    sentinel = existing / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    refused = subprocess.run(["bash", str(script), str(existing)], cwd=tmp_path, env=environment, text=True, capture_output=True, check=False)
    assert refused.returncode != 0 and sentinel.read_text(encoding="utf-8") == "keep"


def test_release_manifest_failure_keeps_cleanup_marker_until_target_is_removed(tmp_path):
    script = ROOT / "scripts" / "build-release.sh"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    failing_shasum = fake_bin / "shasum"
    failing_shasum.write_text("#!/usr/bin/env bash\nexit 73\n", encoding="utf-8")
    failing_shasum.chmod(0o755)
    npm = shutil.which("npm")
    assert npm is not None
    environment = {**dict(__import__("os").environ), "PATH": f"{fake_bin}:{Path(npm).parent}:{Path(sys.executable).parent}:/usr/bin:/bin"}
    failed_target = tmp_path / "manifest-failed-release"

    failed = subprocess.run(
        ["bash", str(script), str(failed_target)],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert failed.returncode == 73
    assert not failed_target.exists()


def test_skip_web_build_rejects_every_dist_manifest_difference_in_an_isolated_copy(tmp_path):
    def copy_source(name: str, source_root: Path = ROOT) -> Path:
        copied = tmp_path / name
        shutil.copytree(
            source_root,
            copied,
            ignore=shutil.ignore_patterns(".git", ".venv", ".worktrees", ".pytest_cache", "node_modules", "__pycache__"),
        )
        return copied

    def skipped(source: Path, output: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(source / "scripts" / "build-release.sh"), "--skip-web-build", str(output)],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=False,
        )

    baseline = copy_source("baseline")
    built = subprocess.run(["bash", str(baseline / "scripts" / "build-release.sh"), str(tmp_path / "built-release")], cwd=tmp_path, text=True, capture_output=True, check=False)
    assert built.returncode == 0, built.stderr
    assert skipped(baseline, tmp_path / "baseline-release").returncode == 0

    mutations = {
        "index": lambda dist: (dist / "index.html").write_text((dist / "index.html").read_text(encoding="utf-8") + "<!-- altered -->", encoding="utf-8"),
        "hashed-js": lambda dist: next((dist / "assets").glob("*.js")).write_bytes(next((dist / "assets").glob("*.js")).read_bytes() + b"altered"),
        "added": lambda dist: (dist / "evil.js").write_text("console.log('unexpected')", encoding="utf-8"),
        "deleted": lambda dist: (dist / "logo.svg").unlink(),
    }
    for name, mutate in mutations.items():
        source = copy_source(name, baseline)
        mutate(source / "web" / "dist")
        output = tmp_path / f"{name}-release"
        result = skipped(source, output)
        assert result.returncode != 0, name
        assert not output.exists(), name

"""Black-box contracts for the patched synthetic Portal fixture.

The test copy and its MockTransport are local only; no configured Portal or
generation-service port is contacted.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import JobState, ModelSpec, UpstreamJob
from ai_creation_canvas.domain.registry import AdapterRegistry


REPO_ROOT = Path(__file__).resolve().parents[2]
PREPARE = REPO_ROOT / "scripts" / "prepare-portal-test-copy.sh"


class FixtureGeneration:
    service_id = "fixture-images"
    requires_portal_cookie = True

    def __init__(self) -> None:
        self.submissions = 0
        self.cookies: list[str] = []

    async def list_models(self, context):
        return (ModelSpec("fixture-image", self.service_id, "Fixture image", ("image.generate",)),)

    async def submit(self, context, request):
        self.submissions += 1
        return UpstreamJob(self.service_id, f"upstream-{self.submissions}", JobState(f"upstream-{self.submissions}", "queued"))

    async def list_models_with_cookie(self, context, cookie_header):
        return await self.list_models(context)

    async def submit_with_cookie(self, context, request, cookie_header):
        self.cookies.append(cookie_header)
        return await self.submit(context, request)

    async def poll(self, context, upstream_job_id):
        return JobState(upstream_job_id, "queued")


def _source_fixture(path: Path) -> None:
    path.mkdir()
    (path / "app.py").write_text(
        """from fastapi import FastAPI, HTTPException, Request

app = FastAPI()
app.state.portal_sessions = {
    "session-a": {"user_id": "user-a", "role": "user", "username": "Alice"},
    "session-b": {"user_id": "user-b", "role": "viewer", "username": "Bob"},
}
app.state.canvas_identity_secret = "fixture-identity-secret"

def authenticated_session(request: Request) -> dict[str, str]:
    user = request.app.state.portal_sessions.get(request.cookies.get("portal_session"))
    if not user:
        raise HTTPException(status_code=401)
    return user

@app.get('/healthz')
def healthz():
    return {'ok': True}
""",
        encoding="utf-8",
    )
    (path / "app_spec.py").write_text("APP_NAME = 'fixture-portal'\n", encoding="utf-8")
    (path / "config.example.json").write_text('{"listen_port": 9090}\n', encoding="utf-8")
    (path / "static").mkdir()
    (path / "static" / "index.html").write_text("fixture", encoding="utf-8")
    (path / "static" / "ssl").mkdir()
    for name in ("server.key", "client.pem", "request-records.json", ".env.local"):
        (path / "static" / "ssl" / name).write_text("do-not-copy", encoding="utf-8")
    (path / "portal").mkdir()
    (path / "portal" / "core.py").write_text("safe = True\n", encoding="utf-8")
    (path / "portal" / "seedance_service.py").write_text("do-not-copy", encoding="utf-8")
    (path / "portal" / ".env.local").write_text("do-not-copy", encoding="utf-8")
    (path / "state").mkdir()
    (path / "state" / "secret.db").write_text("do-not-copy", encoding="utf-8")


def _run_prepare(source: Path, target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(PREPARE), str(source), str(target)], text=True, capture_output=True, check=False)


def _import_portal(target: Path):
    module_name = f"portal_fixture_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(module_name, target / "app.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _as(client: TestClient, session: str) -> TestClient:
    client.cookies.set("portal_session", session)
    return client


@pytest.fixture
def portal(tmp_path):
    source = tmp_path / "portal-source"
    _source_fixture(source)
    target = REPO_ROOT / "work" / f"portal-test-http-{time.time_ns()}"
    result = _run_prepare(source, target)
    assert result.returncode == 0, result.stderr
    module = _import_portal(target)
    adapter = FixtureGeneration()
    registry = AdapterRegistry()
    registry.register_generation(adapter)
    static_dir = tmp_path / "canvas-static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("canvas-spa", encoding="utf-8")
    canvas_app = create_app(
        Settings("test", 8992, tmp_path / "canvas-data", "fixture-identity-secret"),
        static_dir=static_dir,
        registry=registry,
        model_catalog=ModelCatalog(registry),
    )
    canvas_client = TestClient(canvas_app, raise_server_exceptions=False)

    def canvas(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        assert request.url.port == 8992
        response = canvas_client.request(
            request.method,
            request.url.raw_path.decode("ascii"),
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))

    module.app.state.canvas_transport = httpx.MockTransport(canvas)
    try:
        yield TestClient(module.app, raise_server_exceptions=False), canvas_app, adapter, module, target
    finally:
        subprocess.run(["rm", "-rf", str(target)], check=True)


def test_copy_script_applies_runnable_patch_and_excludes_nested_sensitive_files(tmp_path):
    source = tmp_path / "portal-source"
    _source_fixture(source)
    target = REPO_ROOT / "work" / f"portal-test-copy-{time.time_ns()}"
    try:
        result = _run_prepare(source, target)
        assert result.returncode == 0, result.stderr
        assert (target / "portal" / "core.py").exists()
        for path in (
            "state/secret.db", "portal/seedance_service.py", "portal/.env.local",
            "static/ssl/server.key", "static/ssl/client.pem",
            "static/ssl/request-records.json", "static/ssl/.env.local",
        ):
            assert not (target / path).exists()
        assert (target / "ai-canvas-test.json").exists()
    finally:
        if target.exists():
            subprocess.run(["rm", "-rf", str(target)], check=True)


def test_copy_script_refuses_existing_target_keeps_sentinel_and_cleans_mismatch(tmp_path):
    source = tmp_path / "portal-source"
    _source_fixture(source)
    target = REPO_ROOT / "work" / f"portal-test-existing-{time.time_ns()}"
    target.mkdir(parents=True)
    sentinel = target / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    try:
        assert _run_prepare(source, target).returncode != 0
        assert sentinel.read_text(encoding="utf-8") == "keep"
    finally:
        subprocess.run(["rm", "-rf", str(target)], check=True)

    bad = tmp_path / "bad-source"
    _source_fixture(bad)
    (bad / "app.py").write_text("wrong source shape\n", encoding="utf-8")
    mismatch = REPO_ROOT / "work" / f"portal-test-mismatch-{time.time_ns()}"
    assert _run_prepare(bad, mismatch).returncode != 0
    assert not mismatch.exists()


def test_copy_script_rejects_a_symlink_at_any_allowlisted_depth(tmp_path):
    source = tmp_path / "portal-source"
    _source_fixture(source)
    (source / "static" / "link").symlink_to(source / "static" / "index.html")
    target = REPO_ROOT / "work" / f"portal-test-symlink-{time.time_ns()}"
    assert _run_prepare(source, target).returncode != 0
    assert not target.exists()


def test_proxy_strips_all_forged_identity_headers_and_replaces_them_with_session_v2(portal):
    client, _, adapter, _, _ = portal
    response = _as(client, "session-a").get(
        "/ai-canvas/api/v1/session",
        headers={
            "X-Portal-Sig-Version": "999", "X-Portal-Timestamp": "0",
            "X-Portal-User-Id": "user-b", "X-Portal-Username": "Mallory",
            "X-Portal-Role": "admin", "X-Portal-Signature": "forged",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"user_id": "user-a", "role": "user", "username": "Alice"}
    assert "x-job-id" not in response.headers


def test_proxy_rewrites_mount_and_preserves_method_body_and_portal_cookie(portal):
    client, _, adapter, _, _ = portal
    response = _as(client, "session-a").post(
        "/ai-canvas/api/v1/jobs",
        content=b'{"operation":"image.generate","model_id":"fixture-image","prompt":"test","idempotency_key":"job-a"}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "queued"
    assert adapter.cookies == ["portal_session=session-a"]
    assert client.get("/ai-canvas/", headers={"accept": "text/html"}).text == "canvas-spa"
    assert client.get("/ai-canvas/projects/one", headers={"accept": "text/html"}).text == "canvas-spa"
    for unsafe in ("/ai-canvas//api/v1/session", "/ai-canvas/%2e%2e/api/v1/session", "/ai-canvas/http://evil.test"):
        assert client.get(unsafe).status_code == 404


def test_two_users_cannot_read_each_others_results_and_usage_is_once(portal):
    client, canvas_app, adapter, _, _ = portal
    created = _as(client, "session-a").post(
        "/ai-canvas/api/v1/jobs",
        json={"operation": "image.generate", "model_id": "fixture-image", "prompt": "test", "idempotency_key": "job-a"},
    )
    job_id = created.json()["id"]
    assert adapter.submissions == 1
    assert client.get(f"/ai-canvas/api/v1/jobs/{job_id}").status_code == 200
    assert _as(client, "session-b").get(f"/ai-canvas/api/v1/jobs/{job_id}").status_code == 403


def test_proxy_rejects_missing_or_stale_session_and_patch_has_constant_time_v2_verification(portal):
    client, _, _, module, target = portal
    assert client.get("/ai-canvas/api/v1/session").status_code == 401
    headers = module.canvas_identity_headers({"user_id": "user-a", "role": "user", "username": "Alice"}, "fixture-identity-secret")
    assert module.verify_canvas_identity(headers, "fixture-identity-secret", now=int(headers["X-Portal-Timestamp"]))
    headers["X-Portal-Signature"] = "0" * 64
    assert not module.verify_canvas_identity(headers, "fixture-identity-secret", now=int(headers["X-Portal-Timestamp"]))
    stale = module.canvas_identity_headers({"user_id": "user-a", "role": "user", "username": "Alice"}, "fixture-identity-secret")
    assert not module.verify_canvas_identity(stale, "fixture-identity-secret", now=int(stale["X-Portal-Timestamp"]) + 61)
    assert json.loads((target / "ai-canvas-test.json").read_text(encoding="utf-8"))["portal_port"] == 9190

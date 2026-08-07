import asyncio
import hashlib, hmac, time
import sqlite3
from urllib.parse import quote

from fastapi.testclient import TestClient
import httpx

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import JobState, ModelSpec, RequestContext, UpstreamJob
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.api.results import parse_range_header, validate_partial_response
from ai_creation_canvas.errors import PortalUpstreamError
import pytest


class FakeGeneration:
    service_id = "images"
    def __init__(self): self.submit_count = 0
    async def list_models(self, context: RequestContext):
        return (ModelSpec("model-1", self.service_id, "Model", ("image.generate",)),)
    async def submit(self, context, request):
        self.submit_count += 1
        return UpstreamJob(self.service_id, "upstream-1", JobState("upstream-1", "queued"))
    async def poll(self, context, upstream_job_id): return JobState(upstream_job_id, "queued")
    async def fetch_result(self, context, upstream_job_id, result_ref): return (b"abcdef", "image/png")
    async def open_result(self, context, result_id, *, cookie_header, range_header=None, head=False):
        data = b"abcdef"
        content_range = None
        if range_header == "bytes=-2": data, content_range = b"ef", "bytes 4-5/6"
        elif range_header == "bytes=0-": content_range = "bytes 0-5/6"
        class Stream:
            status_code = 206 if range_header else 200
            headers = {"content-type":"image/png", "content-length":str(len(data)), "accept-ranges":"bytes", **({"content-range": content_range} if content_range else {})}
            async def aiter_bytes(self): yield data
            async def aclose(self): pass
        return Stream()


def headers(user="u-a"):
    timestamp = str(int(time.time())); payload = f"v2\n{timestamp}\n{user}\nuser\n{quote('Alice', safe='')}"
    signature = hmac.new(b"test-secret", payload.encode(), hashlib.sha256).hexdigest()
    return {"X-Portal-Sig-Version":"2", "X-Portal-Timestamp":timestamp, "X-Portal-User-Id":user, "X-Portal-Username":"Alice", "X-Portal-Role":"user", "X-Portal-Signature":signature}


def test_idempotency_and_job_ownership(tmp_path):
    adapter = FakeGeneration(); registry = AdapterRegistry(); registry.register_generation(adapter)
    app = create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry))
    client = TestClient(app, raise_server_exceptions=False)
    data = {"operation":"image.generate", "model_id":"model-1", "prompt":"secret prompt", "params":{}, "asset_ids":[], "idempotency_key":"same"}
    first = client.post("/api/v1/jobs", json=data, headers=headers()); second = client.post("/api/v1/jobs", json=data, headers=headers())
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"] and adapter.submit_count == 1
    assert client.get(f"/api/v1/jobs/{first.json()['id']}", headers=headers("u-b")).status_code == 403
    data["prompt"] = "different"
    assert client.post("/api/v1/jobs", json=data, headers=headers()).status_code == 409
    assert "secret prompt" not in (tmp_path / "data" / "canvas.sqlite3").read_bytes().decode(errors="ignore")


def test_protected_result_supports_one_valid_range(tmp_path):
    adapter = FakeGeneration(); registry = AdapterRegistry(); registry.register_generation(adapter)
    app = create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry))
    store = app.state.canvas_store
    store.reserve_job(user_id="u-a", job_id="job", service_id="images", operation="image.generate", idempotency_key="key", request_hash="a" * 64)
    store.mark_submitted("job", "upstream-1", "queued")
    store._update("job", status="succeeded", result_ref="opaque-result")
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/api/v1/results/job", headers={**headers(), "range":"bytes=0-"}).content == b"abcdef"
    assert client.get("/api/v1/results/job", headers={**headers(), "range":"bytes=-2"}).content == b"ef"
    assert client.get("/api/v1/results/job", headers={**headers(), "range":"bytes=0-1,3-4"}).status_code == 416


def _succeeded_result_app(tmp_path, adapter):
    registry = AdapterRegistry(); registry.register_generation(adapter)
    app = create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry))
    store = app.state.canvas_store
    store.reserve_job(user_id="u-a", job_id="job", service_id="images", operation="image.generate", idempotency_key="key", request_hash="a" * 64)
    store.mark_submitted("job", "upstream-1", "queued")
    store._update("job", status="succeeded", result_ref="opaque-result")
    return app


def test_result_rejects_mismatched_upstream_content_range_before_streaming(tmp_path):
    class BadRange(FakeGeneration):
        async def open_result(self, context, result_id, *, cookie_header, range_header=None, head=False):
            class Stream:
                status_code = 206
                headers = {"content-type": "image/png", "content-length": "3", "content-range": "bytes 1-3/6"}
                async def aiter_bytes(self): yield b"bcd"
                async def aclose(self): pass
            return Stream()

    client = TestClient(_succeeded_result_app(tmp_path, BadRange()), raise_server_exceptions=False)
    response = client.get("/api/v1/results/job", headers={**headers(), "range": "bytes=2-4"})
    assert response.status_code == 404
    assert response.json()["code"] == "RESULT_EXPIRED"


def test_result_head_uses_upstream_head_and_invalid_local_range_has_no_range_header(tmp_path):
    class HeadAware(FakeGeneration):
        def __init__(self): super().__init__(); self.head = None
        async def open_result(self, context, result_id, *, cookie_header, range_header=None, head=False):
            self.head = head
            return await super().open_result(context, result_id, cookie_header=cookie_header, range_header=range_header, head=head)

    adapter = HeadAware(); client = TestClient(_succeeded_result_app(tmp_path, adapter), raise_server_exceptions=False)
    head = client.head("/api/v1/results/job", headers=headers())
    assert head.status_code == 200 and head.content == b"" and adapter.head is True
    invalid = client.get("/api/v1/results/job", headers={**headers(), "range": "bytes=4-1"})
    assert invalid.status_code == 416 and "content-range" not in invalid.headers
    assert invalid.json()["code"] == "RANGE_NOT_SATISFIABLE"


def test_result_rejects_an_oversized_first_stream_chunk_and_closes_provider_stream(tmp_path):
    class OversizedFirstChunk(FakeGeneration):
        def __init__(self): super().__init__(); self.closed = False
        async def open_result(self, context, result_id, *, cookie_header, range_header=None, head=False):
            owner = self
            class Stream:
                status_code = 200
                headers = {"content-type": "image/png", "content-length": "2"}
                async def aiter_bytes(self): yield b"too-large"
                async def aclose(self): owner.closed = True
            return Stream()

    adapter = OversizedFirstChunk(); client = TestClient(_succeeded_result_app(tmp_path, adapter), raise_server_exceptions=False)
    response = client.get("/api/v1/results/job", headers=headers())
    assert response.status_code == 502 and response.json()["code"] == "UPSTREAM_UNAVAILABLE"
    assert adapter.closed is True

@pytest.mark.parametrize("value", ["bytes=0-", "bytes=-2", "bytes=0-3"])
def test_range_parser_accepts_single_byte_ranges(value):
    assert parse_range_header(value) is not None

@pytest.mark.parametrize("value", ["bytes=", "bytes=2-1", "bytes=0-1,2-3", "items=0-1"])
def test_range_parser_rejects_invalid_ranges(value):
    with pytest.raises(ValueError): parse_range_header(value)


def test_partial_response_must_match_requested_range_and_declared_length():
    requested = parse_range_header("bytes=2-4")
    assert requested is not None
    validate_partial_response(requested, "bytes 2-4/6", 3)
    with pytest.raises(ValueError, match="partial response"):
        validate_partial_response(requested, "bytes 1-3/6", 3)
    with pytest.raises(ValueError, match="partial response"):
        validate_partial_response(requested, "bytes 2-4/6", 2)


@pytest.mark.anyio
async def test_concurrent_same_key_creates_one_upstream_job(tmp_path):
    class IdempotentGeneration(FakeGeneration):
        def __init__(self):
            super().__init__()
            self.upstream_by_key: dict[str, str] = {}

        async def submit(self, context, request):
            if request.idempotency_key not in self.upstream_by_key:
                self.submit_count += 1
                await asyncio.sleep(0)
                self.upstream_by_key[request.idempotency_key] = "upstream-one"
            upstream_id = self.upstream_by_key[request.idempotency_key]
            return UpstreamJob(self.service_id, upstream_id, JobState(upstream_id, "queued"))

    adapter = IdempotentGeneration(); registry = AdapterRegistry(); registry.register_generation(adapter)
    app = create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry))
    payload = {"operation":"image.generate", "model_id":"model-1", "prompt":"p", "params":{}, "asset_ids":[], "idempotency_key":"concurrent"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first, second = await asyncio.gather(
            client.post("/api/v1/jobs", json=payload, headers=headers()),
            client.post("/api/v1/jobs", json=payload, headers=headers()),
        )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert adapter.submit_count == 1


def test_cookie_protected_catalog_rejects_submission_before_catalog_or_storage(tmp_path):
    class CookieGeneration(FakeGeneration):
        requires_portal_cookie = True
        def __init__(self): super().__init__(); self.catalog_calls = 0
        async def list_models(self, context):
            self.catalog_calls += 1
            return await super().list_models(context)

    adapter = CookieGeneration(); registry = AdapterRegistry(); registry.register_generation(adapter)
    app = create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry))
    client = TestClient(app, raise_server_exceptions=False)
    payload = {"operation":"image.generate", "model_id":"model-1", "prompt":"p", "params":{}, "asset_ids":[], "idempotency_key":"cookie-first"}
    response = client.post("/api/v1/jobs", json=payload, headers=headers())
    assert response.status_code == 401 and response.json()["code"] == "AUTH_REQUIRED"
    assert adapter.catalog_calls == 0
    assert sqlite3.connect(app.state.canvas_store.database).execute("SELECT COUNT(*) FROM canvas_jobs").fetchone()[0] == 0


@pytest.mark.parametrize(("retryable", "expected_status", "expected_code"), [(True, 502, "UPSTREAM_UNAVAILABLE"), (False, 422, "REQUEST_REJECTED")])
def test_typed_submission_error_never_returns_created(tmp_path, retryable, expected_status, expected_code):
    class FailingGeneration(FakeGeneration):
        async def submit(self, context, request):
            raise PortalUpstreamError(retryable=retryable)

    registry = AdapterRegistry(); registry.register_generation(FailingGeneration())
    app = create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry))
    payload = {"operation":"image.generate", "model_id":"model-1", "prompt":"p", "params":{}, "asset_ids":[], "idempotency_key":f"failure-{retryable}"}
    response = TestClient(app, raise_server_exceptions=False).post("/api/v1/jobs", json=payload, headers=headers())
    assert response.status_code == expected_status and response.status_code != 201
    assert response.json()["code"] == expected_code

import asyncio
import hashlib, hmac, json, time
import sqlite3
from urllib.parse import quote

from fastapi.testclient import TestClient
import httpx

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog, PortalJobsAdapter, ServiceDeclaration
from ai_creation_canvas.adapters.portal.client import PortalClient
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import AssetRef, JobState, ModelSpec, RequestContext, UpstreamJob
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.storage.sqlite import CanvasStore
from ai_creation_canvas.api.results import parse_range_header, validate_partial_response
from ai_creation_canvas.errors import InvalidUpstreamResult, PortalUpstreamError
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
    assert client.get(f"/api/v1/jobs/{first.json()['id']}", headers=headers("u-b")).status_code == 404
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
    assert response.status_code == 502
    assert response.json()["code"] == "UPSTREAM_UNAVAILABLE"


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


def test_result_maps_each_upstream_preheader_status_and_closes_it(tmp_path):
    class StatusGeneration(FakeGeneration):
        def __init__(self, status_code):
            super().__init__()
            self.status_code = status_code
            self.closed = 0

        async def open_result(self, context, result_id, *, cookie_header, range_header=None, head=False):
            owner = self
            status_code = self.status_code
            class Stream:
                headers = {"content-range": "bytes */6"} if status_code == 416 else {}
                async def aiter_bytes(self):
                    if False:
                        yield b""
                async def aclose(self):
                    owner.closed += 1
            stream = Stream()
            stream.status_code = status_code
            return stream

    expectations = {
        401: (502, "UPSTREAM_UNAVAILABLE", False),
        403: (502, "UPSTREAM_UNAVAILABLE", False),
        404: (404, "RESULT_EXPIRED", False),
        429: (502, "UPSTREAM_UNAVAILABLE", True),
        500: (502, "UPSTREAM_UNAVAILABLE", True),
        418: (502, "UPSTREAM_UNAVAILABLE", False),
    }
    for upstream_status, expected in expectations.items():
        adapter = StatusGeneration(upstream_status)
        response = TestClient(_succeeded_result_app(tmp_path / str(upstream_status), adapter), raise_server_exceptions=False).get("/api/v1/results/job", headers=headers())
        assert (response.status_code, response.json()["code"], response.json()["retryable"]) == expected
        assert adapter.closed == 1

    adapter = StatusGeneration(416)
    response = TestClient(_succeeded_result_app(tmp_path / "range", adapter), raise_server_exceptions=False).get("/api/v1/results/job", headers={**headers(), "range": "bytes=99-100"})
    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */6"
    assert adapter.closed == 1


def test_result_does_not_forge_416_when_upstream_ignored_a_valid_range(tmp_path):
    class IgnoredRange(FakeGeneration):
        def __init__(self):
            super().__init__()
            self.closed = False
        async def open_result(self, context, result_id, *, cookie_header, range_header=None, head=False):
            owner = self
            class Stream:
                status_code = 200
                headers = {"content-type": "image/png", "content-length": "6"}
                async def aiter_bytes(self): yield b"abcdef"
                async def aclose(self): owner.closed = True
            return Stream()
    adapter = IgnoredRange()
    response = TestClient(_succeeded_result_app(tmp_path, adapter), raise_server_exceptions=False).get("/api/v1/results/job", headers={**headers(), "range": "bytes=0-1"})
    assert response.status_code == 502
    assert response.json()["retryable"] is False
    assert adapter.closed is True


def test_result_rejects_nonidentity_encoding_and_closes_provider_stream(tmp_path):
    class Encoded(FakeGeneration):
        def __init__(self):
            super().__init__()
            self.closed = False
        async def open_result(self, context, result_id, *, cookie_header, range_header=None, head=False):
            owner = self
            class Stream:
                status_code = 200
                headers = {"content-type": "image/png", "content-length": "6", "content-encoding": "gzip"}
                async def aiter_bytes(self): yield b"abcdef"
                async def aclose(self): owner.closed = True
            return Stream()
    adapter = Encoded()
    response = TestClient(_succeeded_result_app(tmp_path, adapter), raise_server_exceptions=False).get("/api/v1/results/job", headers=headers())
    assert response.status_code == 502
    assert adapter.closed is True

@pytest.mark.parametrize("value", ["bytes=0-", "bytes=-2", "bytes=0-3"])
def test_range_parser_accepts_single_byte_ranges(value):
    assert parse_range_header(value) is not None

@pytest.mark.parametrize("value", ["bytes=", "bytes=-0", "bytes=2-1", "bytes=0-1,2-3", "items=0-1"])
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


def test_invalid_succeeded_result_fails_a_queued_job_once_and_stops_polling(tmp_path):
    class InvalidResultGeneration(FakeGeneration):
        def __init__(self):
            super().__init__()
            self.poll_count = 0
        async def poll(self, context, upstream_job_id):
            self.poll_count += 1
            raise InvalidUpstreamResult("missing opaque ID")

    adapter = InvalidResultGeneration()
    registry = AdapterRegistry(); registry.register_generation(adapter)
    app = create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry))
    app.state.canvas_store.reserve_job(user_id="u-a", job_id="job", service_id="images", operation="image.generate", idempotency_key="key", request_hash="a" * 64)
    app.state.canvas_store.mark_submitted("job", "upstream-1", "queued")
    client = TestClient(app, raise_server_exceptions=False)
    first = client.get("/api/v1/jobs/job", headers=headers())
    second = client.get("/api/v1/jobs/job", headers=headers())
    assert first.json()["status"] == second.json()["status"] == "failed"
    assert first.json()["error"]["code"] == "INVALID_UPSTREAM_RESULT"
    assert adapter.poll_count == 1


def test_value_error_from_adapter_success_protocol_fails_once_and_stops_polling(tmp_path):
    class MissingResultGeneration(FakeGeneration):
        def __init__(self):
            super().__init__()
            self.poll_count = 0
        async def poll(self, context, upstream_job_id):
            self.poll_count += 1
            raise ValueError("succeeded result is missing")

    adapter = MissingResultGeneration()
    registry = AdapterRegistry(); registry.register_generation(adapter)
    app = create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry))
    app.state.canvas_store.reserve_job(user_id="u-a", job_id="job", service_id="images", operation="image.generate", idempotency_key="key", request_hash="a" * 64)
    app.state.canvas_store.mark_submitted("job", "upstream-1", "queued")
    client = TestClient(app, raise_server_exceptions=False)
    first = client.get("/api/v1/jobs/job", headers=headers())
    second = client.get("/api/v1/jobs/job", headers=headers())
    assert first.json()["status"] == second.json()["status"] == "failed"
    assert first.json()["error"]["code"] == "INVALID_UPSTREAM_RESULT"
    assert adapter.poll_count == 1


def test_nonopaque_result_returned_by_any_adapter_uses_the_same_cas_failure(tmp_path):
    class NonOpaqueResultGeneration(FakeGeneration):
        async def poll(self, context, upstream_job_id):
            return JobState(upstream_job_id, "succeeded", AssetRef("signed?token=secret", "reference", "active", "application/octet-stream"))

    adapter = NonOpaqueResultGeneration()
    registry = AdapterRegistry(); registry.register_generation(adapter)
    app = create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry))
    app.state.canvas_store.reserve_job(user_id="u-a", job_id="job", service_id="images", operation="image.generate", idempotency_key="key", request_hash="a" * 64)
    app.state.canvas_store.mark_submitted("job", "upstream-1", "running")
    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/jobs/job", headers=headers())
    assert response.json()["status"] == "failed"
    assert response.json()["error"]["code"] == "INVALID_UPSTREAM_RESULT"


def test_poll_forwards_the_current_cookie_to_cookie_protected_adapter(tmp_path):
    class CookiePolling(FakeGeneration):
        requires_portal_cookie = True
        def __init__(self):
            super().__init__()
            self.cookies: list[str] = []
        async def poll_with_cookie(self, context, upstream_job_id, cookie_header):
            self.cookies.append(cookie_header)
            return JobState(upstream_job_id, "queued")

    adapter = CookiePolling()
    registry = AdapterRegistry(); registry.register_generation(adapter)
    app = create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry))
    app.state.canvas_store.reserve_job(user_id="u-a", job_id="job", service_id="images", operation="image.generate", idempotency_key="key", request_hash="a" * 64)
    app.state.canvas_store.mark_submitted("job", "upstream-1", "queued")
    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/jobs/job", headers={**headers(), "cookie": "session=a"})
    assert response.status_code == 200
    assert adapter.cookies == ["session=a"]


def test_retry_after_provider_accepted_a_timed_out_submission_reuses_one_upstream_job(tmp_path):
    class Clock:
        value = 1000.0
        def __call__(self): return self.value
    clock = Clock()
    created: dict[str, str] = {}
    create_count = 0
    fail_first = True

    async def provider(request: httpx.Request) -> httpx.Response:
        nonlocal create_count, fail_first
        if request.url.path.endswith("/api/config"):
            return httpx.Response(200, json={"models": [{"id": "model-1", "display_name": "Model", "operations": ["image.generate"]}]})
        key = json.loads(request.content)["idempotency_key"]
        if key not in created:
            create_count += 1
            created[key] = "provider-job-1"
        if fail_first:
            fail_first = False
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(202, json={"id": created[key], "status": "queued"})

    settings = Settings("test", 8992, tmp_path / "data", "test-secret")
    store = CanvasStore(settings.data_dir, clock=clock)
    declaration = ServiceDeclaration("images", "/image", "image", ("image.generate",))
    portal = PortalClient("https://portal.test", allowed_mounts=("/image",), allowed_methods=("GET", "POST"), transport=httpx.MockTransport(provider))
    adapter = PortalJobsAdapter(declaration, portal)
    registry = AdapterRegistry(); registry.register_generation(adapter)
    payload = {"operation":"image.generate", "model_id":"model-1", "prompt":"p", "params":{}, "asset_ids":[], "idempotency_key":"accepted-timeout"}
    first_app = create_app(settings, registry=registry, model_catalog=ModelCatalog(registry), canvas_store=store)
    first = TestClient(first_app, raise_server_exceptions=False).post("/api/v1/jobs", json=payload, headers={**headers(), "cookie": "session=a"})
    assert first.status_code == 502
    reopened = CanvasStore(settings.data_dir, clock=clock)
    retry_app = create_app(settings, registry=registry, model_catalog=ModelCatalog(registry), canvas_store=reopened)
    retry = TestClient(retry_app, raise_server_exceptions=False).post("/api/v1/jobs", json=payload, headers={**headers(), "cookie": "session=a"})
    assert retry.status_code == 201
    assert create_count == 1
    assert sqlite3.connect(reopened.database).execute("SELECT COUNT(*) FROM canvas_jobs").fetchone()[0] == 1

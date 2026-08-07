import hashlib, hmac, time
from urllib.parse import quote

from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import JobState, ModelSpec, RequestContext, UpstreamJob
from ai_creation_canvas.domain.registry import AdapterRegistry


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
        if range_header == "bytes=-2": data = b"ef"
        class Stream:
            status_code = 206 if range_header else 200
            headers = {"content-type":"image/png", "content-length":str(len(data)), "accept-ranges":"bytes"}
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

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import AssetRef, JobState, ModelSpec, UpstreamJob
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.errors import InvalidUpstreamResult, PortalUpstreamError
from tests.contracts.test_generation_flow import headers


class Portrait:
    service_id = "portal-portrait"
    requires_portal_cookie = True
    def __init__(self): self.received = None
    async def list_models(self, context): return (ModelSpec("portrait-video", self.service_id, "Portrait", ("video.image_to_video",), ("text", "image"), {}, "portrait"),)
    async def list_models_with_cookie(self, context, cookie): return await self.list_models(context)
    async def upload(self, context, asset): raise AssertionError
    async def upload_with_cookie(self, context, asset, source, size, cookie): return AssetRef("upstream-1", "portrait", "processing", asset.mime_type)
    async def get(self, context, asset): raise AssertionError
    async def get_with_cookie(self, context, asset, cookie): return AssetRef(asset, "portrait", "active", "image/png")
    async def submit(self, context, request): raise AssertionError
    async def submit_with_cookie(self, context, request, cookie):
        self.received = request.asset_ids
        return UpstreamJob(self.service_id, "job-upstream", JobState("job-upstream", "queued"))
    async def poll(self, context, job): return JobState(job, "queued")


def test_portrait_local_mapping_hides_upstream_and_enforces_owner(tmp_path):
    adapter = Portrait(); registry = AdapterRegistry(); registry.register_asset(adapter); registry.register_generation(adapter)
    client = TestClient(create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry)), raise_server_exceptions=False)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    created = client.post("/api/v1/assets", files={"file": ("portrait.png", png, "image/png")}, data={"kind": "portrait"}, headers={**headers(), "Cookie": "s=a"})
    assert created.status_code == 201 and "upstream" not in created.text
    local_id = created.json()["asset_id"]
    assert client.get(f"/api/v1/assets/{local_id}", headers={**headers("user-b"), "Cookie": "s=b"}).status_code == 403
    active = client.get(f"/api/v1/assets/{local_id}", headers={**headers(), "Cookie": "s=a"})
    assert active.json()["status"] == "active" and "upstream" not in active.text
    response = client.post("/api/v1/jobs", json={"operation":"video.image_to_video","model_id":"portrait-video","prompt":"wave","params":{},"asset_ids":[local_id],"idempotency_key":"portrait-key"}, headers={**headers(), "Cookie": "s=a"})
    assert response.status_code == 201 and adapter.received == ("upstream-1",)


class FailingPortrait(Portrait):
    def __init__(self, failure):
        super().__init__()
        self.failure = failure

    async def upload_with_cookie(self, context, asset, source, size, cookie):
        raise self.failure

    async def get_with_cookie(self, context, asset, cookie):
        raise self.failure

    async def submit_with_cookie(self, context, request, cookie):
        raise self.failure


def _portrait_client(tmp_path, failure):
    adapter = FailingPortrait(failure)
    registry = AdapterRegistry(); registry.register_asset(adapter); registry.register_generation(adapter)
    app = create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry))
    return adapter, app, TestClient(app, raise_server_exceptions=False)


def _stored_portrait(app, *, status="active"):
    path = app.state.canvas_store.data_dir / "assets" / "portrait.png"
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"x")
    return app.state.canvas_store.create_asset(
        asset_id="local-portrait", user_id="u-a", kind="portrait", mime_type="image/png",
        relative_path="assets/portrait.png", size_bytes=1, status=status,
        service_id="portal-portrait", upstream_asset_id="upstream-portrait",
    )["asset_id"]


@pytest.mark.parametrize(
    ("action", "failure", "expected_status", "expected_code", "retryable"),
    [
        ("upload", PortalUpstreamError(retryable=False, status_code=400), 422, "REQUEST_REJECTED", False),
        ("get", PortalUpstreamError(retryable=True, status_code=408), 502, "UPSTREAM_UNAVAILABLE", True),
        ("get", PortalUpstreamError(retryable=True, status_code=429), 502, "UPSTREAM_UNAVAILABLE", True),
        ("submit", PortalUpstreamError(retryable=True, status_code=500), 502, "UPSTREAM_UNAVAILABLE", True),
        ("upload", InvalidUpstreamResult("malformed group JSON"), 502, "UPSTREAM_INVALID", False),
        ("get", InvalidUpstreamResult("non-object asset JSON"), 502, "UPSTREAM_INVALID", False),
        ("submit", InvalidUpstreamResult("unknown job status"), 502, "UPSTREAM_INVALID", False),
    ],
)
def test_portrait_api_maps_typed_upstream_failures_by_business_path(tmp_path, action, failure, expected_status, expected_code, retryable):
    """The API must preserve typed provider outcomes for group, asset, and job paths."""
    _, app, client = _portrait_client(tmp_path, failure)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    if action == "upload":
        response = client.post("/api/v1/assets", files={"file": ("portrait.png", png, "image/png")}, data={"kind": "portrait"}, headers={**headers(), "Cookie": "s=a"})
    elif action == "get":
        local_id = _stored_portrait(app, status="processing")
        response = client.get(f"/api/v1/assets/{local_id}", headers={**headers(), "Cookie": "s=a"})
    else:
        local_id = _stored_portrait(app)
        response = client.post("/api/v1/jobs", json={"operation":"video.image_to_video","model_id":"portrait-video","prompt":"wave","params":{},"asset_ids":[local_id],"idempotency_key":"portrait-key"}, headers={**headers(), "Cookie": "s=a"})
    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code
    assert response.json()["retryable"] is retryable

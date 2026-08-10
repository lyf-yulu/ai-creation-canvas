from __future__ import annotations

from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import AssetRef, JobState, ModelSpec, UpstreamJob
from ai_creation_canvas.domain.registry import AdapterRegistry
from tests.contracts.test_generation_flow import headers


class Portrait:
    service_id = "portal-portrait"
    requires_portal_cookie = True
    def __init__(self): self.received = None
    async def list_models(self, context): return (ModelSpec("portrait-video", self.service_id, "Portrait", ("video.image_to_video",), ("text", "image"), {}, "portrait"),)
    async def list_models_with_cookie(self, context, cookie): return await self.list_models(context)
    async def upload(self, context, asset): raise AssertionError
    async def upload_with_cookie(self, context, asset, content, filename, cookie): return AssetRef("upstream-1", "portrait", "processing", asset.mime_type)
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

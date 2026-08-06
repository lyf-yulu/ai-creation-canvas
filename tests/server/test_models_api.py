from __future__ import annotations

from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import ModelSpec
from ai_creation_canvas.domain.registry import AdapterRegistry
from tests.server.test_app_security import signed_headers


class Adapter:
    service_id = "image-service"

    async def list_models(self, context):
        return (ModelSpec("image-a", self.service_id, "Image A", ("image.generate",), parameter_schema={"steps": 2}),)

    async def submit(self, context, request): raise NotImplementedError
    async def poll(self, context, upstream_job_id): raise NotImplementedError


def test_models_requires_session_cookie_and_returns_modelspec_json(tmp_path):
    registry = AdapterRegistry()
    registry.register_generation(Adapter())
    app = create_app(
        Settings("test", 8992, tmp_path / "data", "test-secret"),
        static_dir=tmp_path / "dist",
        model_catalog=ModelCatalog(registry),
    )
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/api/v1/models", headers=signed_headers()).status_code == 401
    response = client.get("/api/v1/models", headers={**signed_headers(), "Cookie": "portal_session=only-current"})
    assert response.status_code == 200
    assert response.json() == {
        "models": [{
            "model_id": "image-a", "service_id": "image-service", "display_name": "Image A",
            "operations": ["image.generate"], "input_media": [], "parameter_schema": {"steps": 2},
            "requires_asset_kind": None,
        }],
        "diagnostics": [],
    }

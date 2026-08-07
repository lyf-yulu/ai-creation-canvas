from tests.contracts.test_generation_flow import FakeGeneration, headers
from fastapi.testclient import TestClient
from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.registry import AdapterRegistry


def test_upload_checks_actual_size_and_magic_bytes(tmp_path):
    registry = AdapterRegistry(); registry.register_generation(FakeGeneration())
    client = TestClient(create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry)), raise_server_exceptions=False)
    bad = client.post("/api/v1/assets", files={"file": ("../../bad.png", b"not an image", "image/png")}, headers=headers())
    assert bad.status_code == 415
    large = client.post("/api/v1/assets", files={"file": ("x.png", b"\x89PNG\r\n\x1a\n" + b"x" * (10 * 1024 * 1024), "image/png")}, headers={**headers(), "content-length":"1"})
    assert large.status_code == 413
    assert not list((tmp_path / "data" / "assets").glob(".*.upload"))

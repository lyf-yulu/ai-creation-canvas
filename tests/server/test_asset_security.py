from tests.contracts.test_generation_flow import FakeGeneration, headers
from fastapi.testclient import TestClient
from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.registry import AdapterRegistry
from pathlib import Path
import stat


def test_upload_checks_actual_size_and_magic_bytes(tmp_path):
    registry = AdapterRegistry(); registry.register_generation(FakeGeneration())
    client = TestClient(create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry)), raise_server_exceptions=False)
    bad = client.post("/api/v1/assets", files={"file": ("../../bad.png", b"not an image", "image/png")}, headers=headers())
    assert bad.status_code == 415
    large = client.post("/api/v1/assets", files={"file": ("x.png", b"\x89PNG\r\n\x1a\n" + b"x" * (10 * 1024 * 1024), "image/png")}, headers={**headers(), "content-length":"1"})
    assert large.status_code == 413
    assert not list((tmp_path / "data" / "assets").glob(".*.upload"))


def test_upload_rejects_a_truncated_png_and_keeps_no_file(tmp_path, monkeypatch):
    registry = AdapterRegistry(); registry.register_generation(FakeGeneration())
    client = TestClient(create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry)), raise_server_exceptions=False)
    monkeypatch.setattr(Path, "read_bytes", lambda *_: (_ for _ in ()).throw(AssertionError("upload must stream, not read_bytes")))
    response = client.post("/api/v1/assets", files={"file": ("avatar.png", b"\x89PNG\r\n\x1a\ntruncated", "image/png")}, headers=headers())
    assert response.status_code == 415
    assert not list((tmp_path / "data" / "assets").glob("*"))


def test_valid_asset_is_private_and_written_with_owner_only_permissions(tmp_path):
    registry = AdapterRegistry(); registry.register_generation(FakeGeneration())
    client = TestClient(create_app(Settings("test", 8992, tmp_path / "data", "test-secret"), registry=registry, model_catalog=ModelCatalog(registry)), raise_server_exceptions=False)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    created = client.post("/api/v1/assets", files={"file": ("../../avatar.png", png, "image/png")}, headers=headers())
    assert created.status_code == 201
    assert client.get(f"/api/v1/assets/{created.json()['asset_id']}", headers=headers("u-b")).status_code == 403
    asset_path = next((tmp_path / "data" / "assets").glob("*.png"))
    assert stat.S_IMODE(asset_path.stat().st_mode) == 0o600

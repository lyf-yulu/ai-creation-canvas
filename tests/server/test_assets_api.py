from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.domain.models import AssetRef
from tests.contracts.test_generation_flow import FakeGeneration, headers


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND\xaeB`\x82"
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00audio"


def test_opaque_provider_results_do_not_claim_a_local_upload_media_category():
    result = AssetRef("opaque-result", "reference", "active", "application/octet-stream")

    assert result.media_type is None


def asset_client(tmp_path: Path, **limits: int) -> tuple[TestClient, Path]:
    registry = AdapterRegistry()
    registry.register_generation(FakeGeneration())
    data_dir = tmp_path / "data"
    settings = Settings(
        "test",
        8992,
        data_dir,
        "test-secret",
        max_image_upload_bytes=limits.get("image", 1024),
        max_video_upload_bytes=limits.get("video", 2048),
        max_audio_upload_bytes=limits.get("audio", 1024),
    )
    app = create_app(settings, registry=registry, model_catalog=ModelCatalog(registry))
    return TestClient(app, raise_server_exceptions=False), data_dir


@pytest.mark.parametrize(
    ("media_type", "filename", "mime_type", "payload"),
    [
        ("image", "frame.png", "image/png", PNG),
        ("video", "clip.mp4", "video/mp4", MP4),
        ("audio", "voice.mp3", "audio/mpeg", MP3),
    ],
)
def test_owned_media_upload_returns_only_safe_metadata(tmp_path, media_type, filename, mime_type, payload):
    client, _ = asset_client(tmp_path)

    response = client.post(
        "/api/v1/assets",
        files={"file": (filename, payload, mime_type)},
        data={"kind": "reference", "media_type": media_type},
        headers=headers(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body == {
        "asset_id": body["asset_id"],
        "kind": "reference",
        "status": "active",
        "media_type": media_type,
        "mime_type": mime_type,
        "size_bytes": len(payload),
        "created_at": body["created_at"],
        "content_url": f"/api/v1/assets/{body['asset_id']}/content",
    }
    assert filename not in response.text
    assert "relative_path" not in body
    assert "user_id" not in body


def test_upload_enforces_each_configured_limit_from_stream_not_content_length(tmp_path):
    client, data_dir = asset_client(tmp_path, image=len(PNG) - 1, video=len(MP4) - 1, audio=len(MP3) - 1)
    samples = (("image", "image/png", PNG), ("video", "video/mp4", MP4), ("audio", "audio/mpeg", MP3))

    for media_type, mime_type, payload in samples:
        response = client.post(
            "/api/v1/assets",
            files={"file": ("large.bin", payload, mime_type)},
            data={"media_type": media_type},
            headers={**headers(), "content-length": "1"},
        )
        assert response.status_code == 413
        assert response.json()["code"] == "ASSET_TOO_LARGE"

    assert not list((data_dir / "assets").iterdir())


@pytest.mark.parametrize(
    ("media_type", "mime_type", "payload"),
    [
        ("image", "image/png", b"not-png"),
        ("video", "video/mp4", b"not-mp4"),
        ("video", "video/webm", b"not-webm"),
        ("audio", "audio/mpeg", b"not-mp3"),
        ("audio", "audio/wav", b"not-wave"),
        ("image", "video/mp4", MP4),
    ],
)
def test_upload_rejects_mime_signature_or_media_category_mismatch_and_cleans_temp(tmp_path, media_type, mime_type, payload):
    client, data_dir = asset_client(tmp_path)

    response = client.post(
        "/api/v1/assets",
        files={"file": ("unsafe.bin", payload, mime_type)},
        data={"media_type": media_type},
        headers=headers(),
    )

    assert response.status_code == 415
    assert not list((data_dir / "assets").iterdir())


def test_asset_metadata_and_content_are_owner_isolated(tmp_path):
    client, _ = asset_client(tmp_path)
    created = client.post(
        "/api/v1/assets",
        files={"file": ("frame.png", PNG, "image/png")},
        data={"media_type": "image"},
        headers=headers(),
    ).json()

    assert client.get(f"/api/v1/assets/{created['asset_id']}", headers=headers("u-b")).status_code == 403
    assert client.get(created["content_url"], headers=headers("u-b")).status_code == 403


def test_same_origin_asset_content_supports_get_head_and_single_range(tmp_path):
    client, _ = asset_client(tmp_path)
    created = client.post(
        "/api/v1/assets",
        files={"file": ("clip.mp4", MP4, "video/mp4")},
        data={"media_type": "video"},
        headers=headers(),
    ).json()
    url = created["content_url"]

    full = client.get(url, headers=headers())
    assert full.status_code == 200
    assert full.content == MP4
    assert full.headers["content-type"] == "video/mp4"
    assert full.headers["content-length"] == str(len(MP4))
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["cache-control"] == "private, no-store"

    head = client.head(url, headers=headers())
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(MP4))

    partial = client.get(url, headers={**headers(), "range": "bytes=4-11"})
    assert partial.status_code == 206
    assert partial.content == MP4[4:12]
    assert partial.headers["content-range"] == f"bytes 4-11/{len(MP4)}"
    assert partial.headers["content-length"] == "8"

    invalid = client.get(url, headers={**headers(), "range": "bytes=999-1000"})
    assert invalid.status_code == 416
    assert invalid.headers["content-range"] == f"bytes */{len(MP4)}"


def test_upload_removes_temporary_and_target_files_when_persistence_fails(tmp_path, monkeypatch):
    client, data_dir = asset_client(tmp_path)

    def fail_create_asset(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(client.app.state.canvas_store, "create_asset", fail_create_asset)
    response = client.post(
        "/api/v1/assets",
        files={"file": ("frame.png", PNG, "image/png")},
        data={"media_type": "image"},
        headers=headers(),
    )

    assert response.status_code == 500
    assert not list((data_dir / "assets").iterdir())


def test_content_endpoint_rejects_tampered_paths_outside_the_asset_root(tmp_path):
    client, data_dir = asset_client(tmp_path)
    outside = data_dir / "outside.mp4"
    outside.write_bytes(MP4)
    client.app.state.canvas_store.create_asset(
        asset_id="tampered-asset",
        user_id="u-a",
        kind="reference",
        media_type="video",
        mime_type="video/mp4",
        relative_path="assets/../outside.mp4",
        size_bytes=len(MP4),
    )

    response = client.get("/api/v1/assets/tampered-asset/content", headers=headers())

    assert response.status_code == 404
    assert response.content != MP4

from __future__ import annotations

from pathlib import Path
import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import httpx
import pytest

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.domain.models import AssetRef
from ai_creation_canvas.storage.sqlite import AssetQuotaExceeded, CanvasStore
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
        upload_concurrency=limits.get("concurrency", 4),
        user_asset_quota_bytes=limits.get("user_quota", 2 * 1024 * 1024 * 1024),
        total_asset_quota_bytes=limits.get("total_quota", 10 * 1024 * 1024 * 1024),
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


def test_owner_can_delete_local_reference_asset_and_record_together(tmp_path):
    client, data_dir = asset_client(tmp_path)
    created = client.post(
        "/api/v1/assets",
        files={"file": ("frame.png", PNG, "image/png")},
        data={"kind": "reference", "media_type": "image"},
        headers=headers("u-a"),
    ).json()
    asset_id = created["asset_id"]
    stored, _ = client.app.state.canvas_store.asset_for_owner(asset_id, "u-a")
    assert stored is not None
    path = data_dir / str(stored["relative_path"])
    assert path.is_file()

    response = client.delete(f"/api/v1/assets/{asset_id}", headers=headers("u-a"))

    assert response.status_code == 204
    assert not path.exists()
    assert client.app.state.canvas_store.asset_for_owner(asset_id, "u-a") == (None, False)
    assert client.get(f"/api/v1/assets/{asset_id}", headers=headers("u-a")).status_code == 404


def test_asset_delete_is_owner_isolated_and_refuses_portrait_or_upstream_assets(tmp_path):
    client, data_dir = asset_client(tmp_path)
    store = client.app.state.canvas_store
    local = data_dir / "assets" / "owned.png"
    local.write_bytes(PNG)
    store.create_asset(asset_id="owned-reference", user_id="u-a", kind="reference", media_type="image", mime_type="image/png", relative_path="assets/owned.png", size_bytes=len(PNG))
    portrait = data_dir / "assets" / "portrait.png"
    portrait.write_bytes(PNG)
    store.create_asset(asset_id="owned-portrait", user_id="u-a", kind="portrait", media_type="image", mime_type="image/png", relative_path="assets/portrait.png", size_bytes=len(PNG), service_id="portal-portrait", upstream_asset_id="upstream")

    assert client.delete("/api/v1/assets/owned-reference", headers=headers("u-b")).status_code == 403
    refused = client.delete("/api/v1/assets/owned-portrait", headers=headers("u-a"))

    assert refused.status_code == 409
    assert local.is_file() and portrait.is_file()
    assert store.asset_for_owner("owned-reference", "u-a")[0] is not None
    assert store.asset_for_owner("owned-portrait", "u-a")[0] is not None


def test_asset_delete_rejects_unsafe_path_and_keeps_record(tmp_path):
    client, data_dir = asset_client(tmp_path)
    outside = data_dir / "outside.png"
    outside.write_bytes(PNG)
    store = client.app.state.canvas_store
    store.create_asset(asset_id="unsafe-reference", user_id="u-a", kind="reference", media_type="image", mime_type="image/png", relative_path="assets/../outside.png", size_bytes=len(PNG))

    response = client.delete("/api/v1/assets/unsafe-reference", headers=headers("u-a"))

    assert response.status_code == 404
    assert outside.is_file()
    assert store.asset_for_owner("unsafe-reference", "u-a")[0] is not None


def test_asset_delete_refuses_symlink_and_rename_failure_keeps_file_and_record(tmp_path, monkeypatch):
    client, data_dir = asset_client(tmp_path)
    store = client.app.state.canvas_store
    outside = data_dir / "outside.png"
    outside.write_bytes(PNG)
    link = data_dir / "assets" / "linked.png"
    link.symlink_to(outside)
    store.create_asset(asset_id="linked-reference", user_id="u-a", kind="reference", media_type="image", mime_type="image/png", relative_path="assets/linked.png", size_bytes=len(PNG))

    refused = client.delete("/api/v1/assets/linked-reference", headers=headers("u-a"))

    assert refused.status_code == 404
    assert outside.is_file() and link.is_symlink()
    assert store.asset_for_owner("linked-reference", "u-a")[0] is not None

    regular = data_dir / "assets" / "regular.png"
    regular.write_bytes(PNG)
    store.create_asset(asset_id="regular-reference", user_id="u-a", kind="reference", media_type="image", mime_type="image/png", relative_path="assets/regular.png", size_bytes=len(PNG))
    monkeypatch.setattr("ai_creation_canvas.api.assets.os.rename", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied")))

    failed = client.delete("/api/v1/assets/regular-reference", headers=headers("u-a"))

    assert failed.status_code == 404
    assert regular.is_file()
    assert store.asset_for_owner("regular-reference", "u-a")[0] is not None


def test_asset_delete_restores_original_on_db_commit_failure(tmp_path):
    client, data_dir = asset_client(tmp_path)
    store = client.app.state.canvas_store
    regular = data_dir / "assets" / "regular.png"
    regular.write_bytes(PNG)
    store.create_asset(asset_id="regular-reference", user_id="u-a", kind="reference", media_type="image", mime_type="image/png", relative_path="assets/regular.png", size_bytes=len(PNG))
    store._asset_delete_hook = lambda _phase: (_ for _ in ()).throw(RuntimeError("commit failed"))

    response = client.delete("/api/v1/assets/regular-reference", headers=headers("u-a"))

    assert response.status_code == 500
    assert regular.is_file()
    assert store.asset_for_owner("regular-reference", "u-a")[0] is not None
    assert not list((data_dir / "assets").glob("*.delete"))


def test_asset_delete_commit_survives_tombstone_purge_failure(tmp_path, monkeypatch):
    client, data_dir = asset_client(tmp_path)
    store = client.app.state.canvas_store
    regular = data_dir / "assets" / "regular.png"
    regular.write_bytes(PNG)
    store.create_asset(asset_id="regular-reference", user_id="u-a", kind="reference", media_type="image", mime_type="image/png", relative_path="assets/regular.png", size_bytes=len(PNG))
    monkeypatch.setattr("ai_creation_canvas.api.assets.os.unlink", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied")))

    response = client.delete("/api/v1/assets/regular-reference", headers=headers("u-a"))

    assert response.status_code == 204
    assert not regular.exists()
    assert store.asset_for_owner("regular-reference", "u-a") == (None, False)
    tombstones = list((data_dir / "assets").glob(".*.delete"))
    assert len(tombstones) == 1
    with pytest.raises(AssetQuotaExceeded):
        store.create_asset(asset_id="quota-after-delete", user_id="u-a", kind="reference", media_type="image", mime_type="image/png", relative_path="assets/quota.png", size_bytes=1, user_quota_bytes=100, total_quota_bytes=len(PNG))

    outside = data_dir / "outside-tombstone"
    outside.write_bytes(b"outside")
    symlink = data_dir / "assets" / ("." + "a" * 40 + ".delete")
    symlink.symlink_to(outside)
    foreign = data_dir / "assets" / ".foreign.delete"
    foreign.write_bytes(b"foreign")
    monkeypatch.undo()
    CanvasStore(data_dir)

    assert not tombstones[0].exists()
    assert symlink.is_symlink() and outside.read_bytes() == b"outside"
    assert foreign.read_bytes() == b"foreign"


def test_upload_enforces_each_configured_limit_from_stream_not_content_length(tmp_path):
    client, data_dir = asset_client(tmp_path, image=len(PNG) - 1, video=len(MP4) - 1, audio=len(MP3) - 1)
    samples = (("image", "image/png", "large.png", PNG), ("video", "video/mp4", "large.mp4", MP4), ("audio", "audio/mpeg", "large.mp3", MP3))

    for media_type, mime_type, filename, payload in samples:
        response = client.post(
            "/api/v1/assets",
            files={"file": (filename, payload, mime_type)},
            data={"media_type": media_type},
            headers={**headers(), "content-length": "1"},
        )
        assert response.status_code == 413
        assert response.json()["code"] == "ASSET_TOO_LARGE"

    assert not list((data_dir / "assets").iterdir())


def multipart_body(boundary: str, *, filename: str, mime_type: str, payload: bytes) -> bytes:
    return (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\nreference\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"media_type\"\r\n\r\nvideo\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: {mime_type}\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()


@pytest.mark.anyio
async def test_chunked_multipart_stops_within_one_chunk_of_media_limit_without_prebuffering(tmp_path):
    client, data_dir = asset_client(tmp_path, video=512)
    boundary = "bounded-upload"
    body = multipart_body(boundary, filename="clip.mp4", mime_type="video/mp4", payload=MP4 + b"x" * 4096)
    consumed = 0
    chunk_size = 128

    async def chunks():
        nonlocal consumed
        for offset in range(0, len(body), chunk_size):
            consumed += min(chunk_size, len(body) - offset)
            if consumed > 1400:
                raise AssertionError("the route consumed the complete oversized multipart body")
            yield body[offset:offset + chunk_size]

    transport = httpx.ASGITransport(app=client.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        response = await async_client.post(
            "/api/v1/assets",
            content=chunks(),
            headers={**headers(), "content-type": f"multipart/form-data; boundary={boundary}"},
        )

    assert response.status_code == 413
    assert response.json()["code"] == "ASSET_TOO_LARGE"
    assert consumed <= 512 + 768  # headers plus at most one transport chunk beyond the media limit
    assert not list((data_dir / "assets").iterdir())


@pytest.mark.anyio
async def test_chunked_multipart_rejects_unknown_mime_from_part_headers_before_body(tmp_path):
    client, data_dir = asset_client(tmp_path, video=4096)
    boundary = "unknown-mime"
    body = multipart_body(boundary, filename="clip.mp4", mime_type="application/octet-stream", payload=b"never-consume")
    body_start = body.index(b"\r\n\r\n", body.index(b'filename="clip.mp4"')) + 4

    async def headers_only():
        yield body[:body_start + 1]
        raise AssertionError("unknown MIME should be rejected before its body is consumed")

    transport = httpx.ASGITransport(app=client.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        response = await async_client.post(
            "/api/v1/assets",
            content=headers_only(),
            headers={**headers(), "content-type": f"multipart/form-data; boundary={boundary}"},
        )

    assert response.status_code == 415
    assert response.json()["code"] == "ASSET_INVALID"
    assert not list((data_dir / "assets").iterdir())


@pytest.mark.anyio
async def test_chunked_multipart_bounds_huge_part_header_before_buffering_or_spooling(tmp_path, monkeypatch):
    client, data_dir = asset_client(tmp_path)
    boundary = "huge-header"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"".encode()
        + b"a" * 50_000
        + b".png\"\r\nContent-Type: image/png\r\n\r\n"
        + PNG
        + f"\r\n--{boundary}--\r\n".encode()
    )
    consumed = 0

    async def chunks():
        nonlocal consumed
        for offset in range(0, len(body), 512):
            consumed += min(512, len(body) - offset)
            if consumed > 10_240:
                raise AssertionError("oversized part header was buffered past its bound")
            yield body[offset:offset + 512]

    transport = httpx.ASGITransport(app=client.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        response = await async_client.post("/api/v1/assets", content=chunks(), headers={**headers(), "content-type": f"multipart/form-data; boundary={boundary}"})

    assert response.status_code == 400
    assert consumed <= 10_240
    assert not list((data_dir / "assets").iterdir())


@pytest.mark.anyio
@pytest.mark.parametrize("shape", ["preamble", "epilogue"])
async def test_raw_multipart_budget_stops_unbounded_preamble_or_epilogue_within_one_chunk(tmp_path, shape):
    client, data_dir = asset_client(tmp_path, image=64, video=64, audio=64)
    boundary = "raw-budget"
    valid = multipart_body(boundary, filename="frame.png", mime_type="image/png", payload=PNG)
    chunk = b"x" * 4096
    consumed = 0

    async def chunks():
        nonlocal consumed
        if shape == "preamble":
            oversized = b"x" * (70 * 1024)
            consumed += len(oversized)
            yield oversized
            raise AssertionError("oversized preamble was yielded to the parser")
        if shape == "epilogue":
            consumed += len(valid)
            yield valid
        while consumed < 90_000:
            consumed += len(chunk)
            yield chunk
        raise AssertionError("raw multipart stream was not stopped at its configured budget")

    transport = httpx.ASGITransport(app=client.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        response = await async_client.post("/api/v1/assets", content=chunks(), headers={**headers(), "content-type": f"multipart/form-data; boundary={boundary}"})
        assert not list((data_dir / "assets").iterdir())
        followup = await async_client.post("/api/v1/assets", files={"file": ("frame.png", PNG, "image/png")}, data={"media_type": "image"}, headers=headers())

    assert response.status_code == 413
    assert response.json()["code"] == "ASSET_TOO_LARGE"
    maximum_consumed = 70 * 1024 if shape == "preamble" else 64 * 1024 + 64 + len(chunk)
    assert consumed <= maximum_consumed
    assert followup.status_code == 201


def test_oversized_content_length_is_rejected_before_taking_upload_slot(tmp_path, monkeypatch):
    client, _ = asset_client(tmp_path, image=64, video=64, audio=64)
    monkeypatch.setattr("ai_creation_canvas.api.assets.LimitedMediaMultiPartParser", lambda *_args: pytest.fail("parser must not be constructed"))

    response = client.post("/api/v1/assets", content=b"x", headers={**headers(), "content-type": "multipart/form-data; boundary=x", "content-length": str(70 * 1024)})

    assert response.status_code == 413


def test_invalid_multipart_boundary_maps_parser_failure_to_safe_400(tmp_path):
    client, data_dir = asset_client(tmp_path)

    response = client.post("/api/v1/assets", content=b"not-a-multipart-body", headers={**headers(), "content-type": "multipart/form-data; boundary=broken"})

    assert response.status_code == 400
    assert response.json()["code"] == "ASSET_INVALID"
    assert not list((data_dir / "assets").iterdir())


@pytest.mark.anyio
async def test_app_wide_upload_semaphore_limits_parsing_before_multipart_work(tmp_path, monkeypatch):
    client, _ = asset_client(tmp_path, concurrency=1)
    from ai_creation_canvas.api import assets as assets_api
    real_parse = assets_api.LimitedMediaMultiPartParser.parse
    active = 0
    max_active = 0

    async def tracking_parse(parser):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.02)
            return await real_parse(parser)
        finally:
            active -= 1

    monkeypatch.setattr(assets_api.LimitedMediaMultiPartParser, "parse", tracking_parse)
    transport = httpx.ASGITransport(app=client.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        responses = await asyncio.gather(*[
            async_client.post("/api/v1/assets", files={"file": (f"frame-{index}.png", PNG, "image/png")}, data={"media_type": "image"}, headers=headers())
            for index in range(2)
        ])

    assert [response.status_code for response in responses] == [201, 201]
    assert max_active == 1


def test_upload_quota_rejects_before_a_second_asset_record_and_cleans_file(tmp_path):
    client, data_dir = asset_client(tmp_path, user_quota=len(PNG), total_quota=len(PNG))
    request = {"files": {"file": ("frame.png", PNG, "image/png")}, "data": {"media_type": "image"}, "headers": headers()}

    first = client.post("/api/v1/assets", **request)
    second = client.post("/api/v1/assets", **request)

    assert first.status_code == 201
    assert second.status_code == 413
    assert second.json()["code"] == "ASSET_QUOTA_EXCEEDED"
    assert len([path for path in (data_dir / "assets").iterdir() if not path.name.startswith(".")]) == 1


def test_asset_quota_sum_and_insert_are_atomic_under_concurrent_writers(tmp_path):
    store = CanvasStore(tmp_path / "quota-store")

    def create(index: int) -> str:
        try:
            store.create_asset(asset_id=f"asset-{index}", user_id="u-a", kind="reference", media_type="image", mime_type="image/png", relative_path=f"assets/{index}.png", size_bytes=15, user_quota_bytes=20, total_quota_bytes=20)
            return "created"
        except AssetQuotaExceeded:
            return "quota"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create, (1, 2)))

    assert sorted(outcomes) == ["created", "quota"]


@pytest.mark.anyio
async def test_cancelled_chunked_upload_closes_starlette_spool_and_leaves_no_app_temp(tmp_path, monkeypatch):
    client, data_dir = asset_client(tmp_path, video=4096)
    boundary = "cancelled-upload"
    prefix = multipart_body(boundary, filename="clip.mp4", mime_type="video/mp4", payload=MP4 + b"x" * 512)
    opened = []
    from tempfile import SpooledTemporaryFile as RealSpooledTemporaryFile

    def tracking_spool(*args, **kwargs):
        file = RealSpooledTemporaryFile(*args, **kwargs)
        opened.append(file)
        return file

    monkeypatch.setattr("starlette.formparsers.SpooledTemporaryFile", tracking_spool)

    async def cancelled_body():
        yield prefix[:400]
        raise asyncio.CancelledError

    transport = httpx.ASGITransport(app=client.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        response = await async_client.post(
            "/api/v1/assets",
            content=cancelled_body(),
            headers={**headers(), "content-type": f"multipart/form-data; boundary={boundary}"},
        )

    assert response.status_code == 500  # ASGITransport converts the cancelled request body into a disconnect.
    assert opened and all(file.closed for file in opened)
    assert not list((data_dir / "assets").iterdir())


@pytest.mark.parametrize(
    ("media_type", "mime_type", "payload"),
    [
        ("image", "image/png", b"not-png"),
        ("video", "video/mp4", b"not-mp4"),
        ("video", "video/webm", b"not-webm"),
        ("audio", "audio/mpeg", b"not-mp3"),
        ("audio", "audio/wav", b"not-wave"),
    ],
)
def test_upload_rejects_invalid_signature_after_reaching_bounded_content_validation(tmp_path, monkeypatch, media_type, mime_type, payload):
    client, data_dir = asset_client(tmp_path)
    extension = {"image/png": "png", "video/mp4": "mp4", "video/webm": "webm", "audio/mpeg": "mp3", "audio/wav": "wav"}[mime_type]
    calls = []
    from ai_creation_canvas.api import assets as assets_api
    real_validate = assets_api._is_valid

    def tracking_validate(*args, **kwargs):
        calls.append((args, kwargs))
        return real_validate(*args, **kwargs)

    monkeypatch.setattr(assets_api, "_is_valid", tracking_validate)

    response = client.post(
        "/api/v1/assets",
        files={"file": (f"invalid.{extension}", payload, mime_type)},
        data={"media_type": media_type},
        headers=headers(),
    )

    assert response.status_code == 415
    assert len(calls) == 1
    assert not list((data_dir / "assets").iterdir())


def test_upload_rejects_media_category_mismatch_before_content_validation(tmp_path, monkeypatch):
    client, data_dir = asset_client(tmp_path)
    monkeypatch.setattr("ai_creation_canvas.api.assets._is_valid", lambda *_args: pytest.fail("category mismatch must not reach signature validation"))

    response = client.post(
        "/api/v1/assets",
        files={"file": ("clip.mp4", MP4, "video/mp4")},
        data={"media_type": "image"},
        headers=headers(),
    )

    assert response.status_code == 415
    assert not list((data_dir / "assets").iterdir())


@pytest.mark.parametrize(
    ("filename", "mime_type", "payload", "expected"),
    [
        ("FRAME.PNG", "image/png", PNG, 201),
        ("scene.v2.png", "image/png", PNG, 201),
        ("project.final.mp4", "video/mp4", MP4, 201),
        ("frame", "image/png", PNG, 415),
        (".png", "image/png", PNG, 415),
        ("frame.", "image/png", PNG, 415),
        ("frame.png.exe", "image/png", PNG, 415),
        ("frame.jpg", "image/png", PNG, 415),
        ("clip.mov", "video/mp4", MP4, 415),
    ],
)
def test_upload_filename_extension_must_match_declared_mime(tmp_path, filename, mime_type, payload, expected):
    client, data_dir = asset_client(tmp_path)
    media_type = mime_type.split("/", 1)[0]

    response = client.post(
        "/api/v1/assets",
        files={"file": (filename, payload, mime_type)},
        data={"media_type": media_type},
        headers=headers(),
    )

    assert response.status_code == expected
    if expected != 201:
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
    assert invalid.headers["cache-control"] == "private, no-store"


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

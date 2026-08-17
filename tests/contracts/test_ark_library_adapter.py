from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from ai_creation_canvas.adapters.ark_assets import ArkAssetLibraryAdapter, tos_presigned_get_url
from ai_creation_canvas.asset_library_config import AssetLibraryConfig
from ai_creation_canvas.domain.models import (
    AssetKind,
    AssetRef,
    AssetStatus,
    PortalRole,
    PortalUser,
    RequestContext,
)
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.errors import AdapterRegistrationError, InvalidUpstreamResult, PortalUpstreamError


FIXED_NOW = datetime(2026, 8, 17, 3, 4, 5, tzinfo=timezone.utc)
_ARK_AK, _ARK_SK = "AK-TEST", "SK-TEST-0123456789"
_TOS_AK, _TOS_SK = "TOS-AK-TEST", "TOS-SK-TEST"


def png() -> bytes:
    # Minimal valid PNG signature plus padding; the adapter never parses the image.
    return b"\x89PNG\r\n\x1a\n" + bytes(64)


def context() -> RequestContext:
    return RequestContext(PortalUser("user-a", "Alice", PortalRole.USER), "request-a", "trace-a")


def library_config() -> AssetLibraryConfig:
    return AssetLibraryConfig(
        ark_access_key=_ARK_AK, ark_secret_key=_ARK_SK,
        tos_access_key=_TOS_AK, tos_secret_key=_TOS_SK,
        tos_bucket="canvas-uploads", tos_region="cn-beijing", project_name="Seedance2.0",
    )


class GroupState:
    def __init__(self, group_id: str | None) -> None:
        self.group_id = group_id
        self.sets: list[str] = []

    def getter(self) -> str | None:
        return self.group_id

    def setter(self, group_id: str) -> None:
        self.group_id = group_id
        self.sets.append(group_id)


def make_adapter(
    handler,
    *,
    group_state: GroupState | None = None,
    attempts: int = 1,
    interval: float = 0.0,
) -> tuple[ArkAssetLibraryAdapter, GroupState]:
    group_state = group_state or GroupState("asset-grp-1")
    return (
        ArkAssetLibraryAdapter(
            config=library_config(),
            group_id_getter=group_state.getter,
            group_id_setter=group_state.setter,
            transport=httpx.MockTransport(handler),
            now=lambda: FIXED_NOW,
            get_asset_attempts=attempts,
            get_asset_interval=interval,
        ),
        group_state,
    )


def source_file(tmp_path: Path) -> tuple[Path, bytes]:
    body = png()
    source = tmp_path / "portrait.png"
    source.write_bytes(body)
    return source, body


def test_upload_put_tos_then_create_asset_then_poll_get_asset(tmp_path: Path) -> None:
    async def scenario() -> None:
        seen: list[httpx.Request] = []
        source, body_bytes = source_file(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.host == "canvas-uploads.tos-cn-beijing.volces.com":
                assert request.method == "PUT"
                assert request.headers["x-tos-content-sha256"] == hashlib.sha256(body_bytes).hexdigest()
                assert request.headers["authorization"].startswith(f"TOS4-HMAC-SHA256 Credential={_TOS_AK}/20260817/cn-beijing/tos/request,")
                assert _TOS_SK not in request.headers["authorization"]
                return httpx.Response(200)
            assert request.url.host == "ark.cn-beijing.volcengineapi.com"
            assert request.method == "POST" and request.url.path == "/"
            if request.url.query.decode("ascii") == "Action=CreateAsset&Version=2024-01-01":
                assert request.headers["authorization"].startswith(f"HMAC-SHA256 Credential={_ARK_AK}/20260817/cn-beijing/ark/request,")
                assert _ARK_SK not in request.headers["authorization"]
                return httpx.Response(200, json={"Result": {"Id": "asset-abc", "Status": "Processing"}})
            assert request.url.query.decode("ascii") == "Action=GetAsset&Version=2024-01-01"
            return httpx.Response(200, json={"Result": {"Id": "asset-abc", "Status": "Active", "AssetType": "Image"}})

        adapter, _ = make_adapter(handler)

        upstream = await adapter.upload_with_file(
            context(), AssetRef("local-1", "library", "processing", "image/png"), source, len(body_bytes),
        )

        assert upstream.asset_id == "asset-abc" and upstream.kind is AssetKind.LIBRARY
        assert upstream.status is AssetStatus.ACTIVE and upstream.mime_type == "image/png"
        assert [request.url.host for request in seen] == [
            "canvas-uploads.tos-cn-beijing.volces.com",
            "ark.cn-beijing.volcengineapi.com",
            "ark.cn-beijing.volcengineapi.com",
        ]
        object_key = seen[0].url.path.lstrip("/")
        assert object_key.startswith("refmedia/") and object_key.endswith(".png")
        created = json.loads(seen[1].content)
        assert created == {
            "GroupId": "asset-grp-1",
            "URL": tos_presigned_get_url(_TOS_AK, _TOS_SK, "canvas-uploads", "cn-beijing", object_key, now=FIXED_NOW),
            "AssetType": "Image",
            "ProjectName": "Seedance2.0",
            "Name": "portrait",
        }
        assert seen[1].headers["x-content-sha256"] == hashlib.sha256(seen[1].content).hexdigest()
        assert json.loads(seen[2].content) == {"Id": "asset-abc", "ProjectName": "Seedance2.0"}
        assert "SK-TEST" not in str(seen[1].headers) and "TOS-SK-TEST" not in str(seen[0].headers)

    asyncio.run(scenario())


def test_upload_returns_processing_without_poll_attempts(tmp_path: Path) -> None:
    async def scenario() -> None:
        seen: list[httpx.Request] = []
        source, body_bytes = source_file(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.host.startswith("canvas-uploads"):
                return httpx.Response(200)
            assert request.url.query.decode("ascii") == "Action=CreateAsset&Version=2024-01-01"
            return httpx.Response(200, json={"Result": {"Id": "asset-abc", "Status": "Processing"}})

        adapter, _ = make_adapter(handler, attempts=0)

        upstream = await adapter.upload_with_file(
            context(), AssetRef("local-1", "library", "processing", "image/png"), source, len(body_bytes),
        )

        assert upstream.status is AssetStatus.PROCESSING
        assert [request.url.host for request in seen] == [
            "canvas-uploads.tos-cn-beijing.volces.com",
            "ark.cn-beijing.volcengineapi.com",
        ]

    asyncio.run(scenario())


def test_ensure_default_group_creates_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        actions: list[str] = []
        source, body_bytes = source_file(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host.startswith("canvas-uploads"):
                return httpx.Response(200)
            if request.url.query.decode("ascii") == "Action=CreateAssetGroup&Version=2024-01-01":
                actions.append("group")
                return httpx.Response(200, json={"Result": {"Id": "asset-grp-new"}})
            if request.url.query.decode("ascii") == "Action=CreateAsset&Version=2024-01-01":
                actions.append("create")
                return httpx.Response(200, json={"Result": {"Id": "asset-abc", "Status": "Processing"}})
            return httpx.Response(200, json={"Result": {"Id": "asset-abc", "Status": "Active"}})

        adapter, group_state = make_adapter(handler, group_state=GroupState(None))
        first = await adapter.upload_with_file(
            context(), AssetRef("local-1", "library", "processing", "image/png"), source, len(body_bytes),
        )
        second = await adapter.upload_with_file(
            context(), AssetRef("local-2", "library", "processing", "image/png"), source, len(body_bytes),
        )

        assert first.status is AssetStatus.ACTIVE and second.status is AssetStatus.ACTIVE
        assert actions.count("group") == 1 and actions.count("create") == 2
        assert group_state.group_id == "asset-grp-new" and group_state.sets == ["asset-grp-new"]

    asyncio.run(scenario())


def test_upload_polls_until_terminal_status(tmp_path: Path) -> None:
    async def scenario() -> None:
        statuses = ["Processing", "Processing", "Active"]
        source, body_bytes = source_file(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host.startswith("canvas-uploads"):
                return httpx.Response(200)
            if request.url.query.decode("ascii").startswith("Action=CreateAsset"):
                return httpx.Response(200, json={"Result": {"Id": "asset-abc", "Status": "Processing"}})
            status = statuses.pop(0) if statuses else "Active"
            return httpx.Response(200, json={"Result": {"Id": "asset-abc", "Status": status}})

        adapter, _ = make_adapter(handler, attempts=3)

        upstream = await adapter.upload_with_file(
            context(), AssetRef("local-1", "library", "processing", "image/png"), source, len(body_bytes),
        )

        assert upstream.status is AssetStatus.ACTIVE and statuses == []

    asyncio.run(scenario())


def test_get_maps_statuses() -> None:
    async def scenario() -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.query.decode("ascii"))
            identifier = json.loads(request.content)["Id"]
            status = {"asset-a": "Active", "asset-b": "Processing", "asset-c": "Failed"}[identifier]
            return httpx.Response(200, json={"Result": {"Id": identifier, "Status": status}})

        adapter, _ = make_adapter(handler)

        assert (await adapter.get(context(), "asset-a")).status is AssetStatus.ACTIVE
        assert (await adapter.get(context(), "asset-b")).status is AssetStatus.PROCESSING
        assert (await adapter.get(context(), "asset-c")).status is AssetStatus.FAILED
        assert seen == ["Action=GetAsset&Version=2024-01-01"] * 3

    asyncio.run(scenario())


def test_upstream_errors_map_to_retryable_portal_errors(tmp_path: Path) -> None:
    async def scenario() -> None:
        source, body_bytes = source_file(tmp_path)

        adapter, _ = make_adapter(lambda request: httpx.Response(429))
        with pytest.raises(PortalUpstreamError) as error:
            await adapter.upload_with_file(
                context(), AssetRef("local-1", "library", "processing", "image/png"), source, len(body_bytes),
            )
        assert error.value.retryable is True and error.value.status_code == 429

        adapter, _ = make_adapter(lambda request: httpx.Response(400, json={"ResponseMetadata": {"Error": {"Code": "Bad"}}}))
        with pytest.raises(PortalUpstreamError) as error:
            await adapter.upload_with_file(
                context(), AssetRef("local-1", "library", "processing", "image/png"), source, len(body_bytes),
            )
        assert error.value.retryable is False and error.value.status_code == 400

    asyncio.run(scenario())


def test_invalid_upstream_payloads_are_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        source, body_bytes = source_file(tmp_path)

        adapter, _ = make_adapter(lambda request: httpx.Response(200, content=b"not-json"))
        with pytest.raises(InvalidUpstreamResult):
            await adapter.upload_with_file(
                context(), AssetRef("local-1", "library", "processing", "image/png"), source, len(body_bytes),
            )

        adapter, _ = make_adapter(lambda request: httpx.Response(200, json={"Result": {"Status": "Active"}}))
        with pytest.raises(InvalidUpstreamResult):
            await adapter.upload_with_file(
                context(), AssetRef("local-1", "library", "processing", "image/png"), source, len(body_bytes),
            )

        adapter, _ = make_adapter(lambda request: httpx.Response(200, json={"Result": {"Id": "asset-abc", "Status": "Weird"}}))
        with pytest.raises(InvalidUpstreamResult):
            await adapter.get(context(), "asset-abc")

    asyncio.run(scenario())


def test_upload_rejects_invalid_sources(tmp_path: Path) -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request may be sent for invalid sources")

        adapter, _ = make_adapter(handler)
        source, body_bytes = source_file(tmp_path)
        link = tmp_path / "portrait-link.png"
        link.symlink_to(source)

        with pytest.raises(ValueError, match="library upload is invalid"):
            await adapter.upload_with_file(context(), AssetRef("local-1", "reference", "active", "image/png"), source, len(body_bytes))
        with pytest.raises(ValueError, match="library upload is invalid"):
            await adapter.upload_with_file(context(), AssetRef("local-1", "library", "processing", "video/mp4"), source, len(body_bytes))
        with pytest.raises(ValueError, match="library upload is invalid"):
            await adapter.upload_with_file(context(), AssetRef("local-1", "library", "processing", "image/png"), link, len(body_bytes))
        with pytest.raises(ValueError, match="library upload is invalid"):
            await adapter.upload_with_file(context(), AssetRef("local-1", "library", "processing", "image/png"), source, len(body_bytes) + 1)
        with pytest.raises(ValueError, match="library upload is invalid"):
            await adapter.upload_with_file(context(), AssetRef("local-1", "library", "processing", "image/png"), source, 10 * 1024 * 1024 + 1)

    asyncio.run(scenario())


def test_register_asset_service_and_duplicate() -> None:
    registry = AdapterRegistry()
    adapter = ArkAssetLibraryAdapter(
        config=library_config(), group_id_getter=lambda: None, group_id_setter=lambda gid: None,
    )
    registry.register_asset(adapter)
    assert registry.asset("ark-video") is adapter
    with pytest.raises(AdapterRegistrationError, match="duplicate service_id"):
        registry.register_asset(adapter)


def test_constructor_rejects_missing_group_hooks_and_bad_poll_knobs() -> None:
    with pytest.raises(ValueError):
        ArkAssetLibraryAdapter(config=library_config(), group_id_getter=None, group_id_setter=lambda gid: None)
    with pytest.raises(ValueError):
        ArkAssetLibraryAdapter(config=library_config(), group_id_getter=lambda: None, group_id_setter=lambda gid: None, get_asset_attempts=-1)
    with pytest.raises(ValueError):
        ArkAssetLibraryAdapter(config=library_config(), group_id_getter=lambda: None, group_id_setter=lambda gid: None, get_asset_interval=-0.5)
    with pytest.raises(TypeError):
        ArkAssetLibraryAdapter(config="not-a-config", group_id_getter=lambda: None, group_id_setter=lambda gid: None)


def test_upload_requires_file_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent for the protocol-only upload")

    adapter, _ = make_adapter(handler)

    async def scenario() -> None:
        with pytest.raises(ValueError, match="library upload requires file bytes"):
            await adapter.upload(context(), AssetRef("local-1", "library", "processing", "image/png"))

    asyncio.run(scenario())

"""Volcano Ark private portrait asset library adapter (AIGC groups only).

Uploads portrait images into the Ark asset library via OpenAPI v4 AK/SK
signing and TOS-signed object storage, then exposes them to the canvas as
upstream asset references. Generation renders these as ``asset://<id>``
URLs, the official way to use private-library portraits with Seedance.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable, Mapping
from urllib.parse import quote
import uuid

import httpx

from ai_creation_canvas.asset_library_config import AssetLibraryConfig
from ai_creation_canvas.domain.models import AssetKind, AssetRef, AssetStatus, RequestContext
from ai_creation_canvas.errors import InvalidUpstreamResult, PortalUpstreamError


_HOST = "ark.cn-beijing.volcengineapi.com"
_VERSION = "2024-01-01"
_ARK_ASSET_ID = re.compile(r"asset-[A-Za-z0-9_-]{1,100}\Z")
_GROUP_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_OPAQUE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_STATUSES = {"Processing": AssetStatus.PROCESSING, "Active": AssetStatus.ACTIVE, "Failed": AssetStatus.FAILED}
_DEFAULT_GROUP_NAME = "canvas-aigc-default"
_PRESIGNED_EXPIRES = 43200


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _amz_date(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


def _signing_key(secret: str, date_stamp: str, region: str, service: str) -> bytes:
    k_date = _sign(secret.encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "request")


def openapi_v4_sign(
    ak: str,
    sk: str,
    method: str,
    host: str,
    uri: str,
    query: str,
    payload: bytes | None,
    *,
    now: datetime,
) -> tuple[str, dict[str, str]]:
    """Sign a Volcengine OpenAPI request. Returns (Authorization, headers)."""
    amz_date = _amz_date(now)
    date_stamp = amz_date[:8]
    region = "cn-beijing"
    service = "ark"
    payload_hash = _sha256_hex(payload or b"")
    headers = {"Host": host, "X-Date": amz_date, "X-Content-Sha256": payload_hash}
    if payload:
        headers["Content-Type"] = "application/json"
    canonical_headers = "".join(f"{name.lower()}:{headers[name].strip()}\n" for name in sorted(headers, key=str.lower))
    signed_headers = ";".join(name.lower() for name in sorted(headers, key=str.lower))
    canonical_request = f"{method}\n{uri}\n{query}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    credential_scope = f"{date_stamp}/{region}/{service}/request"
    string_to_sign = f"HMAC-SHA256\n{amz_date}\n{credential_scope}\n{_sha256_hex(canonical_request)}"
    signature = hmac.new(_signing_key(sk, date_stamp, region, service), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"HMAC-SHA256 Credential={ak}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return authorization, headers


def tos_sign_put(
    ak: str,
    sk: str,
    bucket: str,
    region: str,
    object_key: str,
    mime: str,
    body: bytes,
    *,
    now: datetime,
) -> dict[str, str]:
    """Sign a TOS object PUT. Returns the complete header mapping (no Content-Length)."""
    host = f"{bucket}.tos-{region}.volces.com"
    amz_date = _amz_date(now)
    date_stamp = amz_date[:8]
    payload_hash = _sha256_hex(body)
    headers = {
        "Host": host,
        "Content-Type": mime,
        "x-tos-content-sha256": payload_hash,
        "x-tos-date": amz_date,
    }
    signed = sorted(headers, key=str.lower)
    canonical_headers = "".join(f"{name.lower()}:{headers[name].strip()}\n" for name in signed)
    signed_headers = ";".join(name.lower() for name in signed)
    canonical_uri = "/" + quote(object_key, safe="/")
    canonical_request = f"PUT\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    credential_scope = f"{date_stamp}/{region}/tos/request"
    string_to_sign = f"TOS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n{_sha256_hex(canonical_request.encode('utf-8'))}"
    signature = hmac.new(_signing_key(sk, date_stamp, region, "tos"), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        f"TOS4-HMAC-SHA256 Credential={ak}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return headers


def tos_presigned_get_url(
    ak: str,
    sk: str,
    bucket: str,
    region: str,
    object_key: str,
    *,
    now: datetime,
    expires: int = _PRESIGNED_EXPIRES,
) -> str:
    """Build a query-signed TOS GET URL for the uploaded object."""
    host = f"{bucket}.tos-{region}.volces.com"
    amz_date = _amz_date(now)
    date_stamp = amz_date[:8]
    credential_scope = f"{date_stamp}/{region}/tos/request"
    credential = f"{ak}/{credential_scope}"
    query_values = {
        "X-Tos-Algorithm": "TOS4-HMAC-SHA256",
        "X-Tos-Credential": credential,
        "X-Tos-Date": amz_date,
        "X-Tos-Expires": str(expires),
        "X-Tos-SignedHeaders": "host",
    }
    canonical_query = "&".join(f"{quote(name, safe='')}={quote(query_values[name], safe='')}" for name in sorted(query_values))
    canonical_uri = "/" + quote(object_key, safe="/")
    canonical_headers = f"host:{host}\n"
    canonical_request = f"GET\n{canonical_uri}\n{canonical_query}\n{canonical_headers}\nhost\nUNSIGNED-PAYLOAD"
    string_to_sign = f"TOS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n{_sha256_hex(canonical_request.encode('utf-8'))}"
    signature = hmac.new(_signing_key(sk, date_stamp, region, "tos"), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"https://{host}{canonical_uri}?{canonical_query}&X-Tos-Signature={signature}"


class ArkAssetLibraryAdapter:
    """AssetPort adapter backed by the Ark private portrait asset library."""

    service_id = "ark-video"

    def __init__(
        self,
        *,
        config: AssetLibraryConfig,
        group_id_getter: Callable[[], str | None],
        group_id_setter: Callable[[str], None],
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
        upload_semaphore: asyncio.Semaphore | None = None,
        config_getter: Callable[[], AssetLibraryConfig] | None = None,
        get_asset_attempts: int = 30,
        get_asset_interval: float = 1.0,
    ) -> None:
        if not isinstance(config, AssetLibraryConfig):
            raise TypeError("asset library config is required")
        if config_getter is not None and not callable(config_getter):
            raise ValueError("asset library config getter must be callable")
        if not callable(group_id_getter) or not callable(group_id_setter):
            raise ValueError("asset library group hooks are required")
        if type(get_asset_attempts) is not int or get_asset_attempts < 0:
            raise ValueError("get_asset_attempts must be a non-negative integer")
        if type(get_asset_interval) is not float and type(get_asset_interval) is not int or get_asset_interval < 0:
            raise ValueError("get_asset_interval must be a non-negative number")
        if now is not None and not callable(now):
            raise ValueError("now must be callable")
        self._config = config
        self._config_getter = config_getter or (lambda: self._config)
        self._group_id_getter = group_id_getter
        self._group_id_setter = group_id_setter
        self._transport = transport
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._upload_semaphore = upload_semaphore
        self._get_asset_attempts = get_asset_attempts
        self._get_asset_interval = get_asset_interval

    def _library_config(self) -> AssetLibraryConfig:
        return self._config_getter()

    async def upload(self, context: RequestContext, asset: AssetRef) -> AssetRef:
        raise ValueError("library upload requires file bytes")

    async def upload_with_file(self, context: RequestContext, asset: AssetRef, source: Path, size: int) -> AssetRef:
        if (
            asset.kind is not AssetKind.LIBRARY
            or asset.mime_type not in _IMAGE_MIMES
            or not isinstance(source, Path)
            or not isinstance(size, int)
            or size < 1
            or size > _MAX_IMAGE_BYTES
        ):
            raise ValueError("library upload is invalid")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(source, flags)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != size:
                os.close(fd)
                raise ValueError("library upload is invalid")
        except OSError as error:
            raise ValueError("library upload is invalid") from error
        try:
            async with (self._upload_semaphore or nullcontext()):
                group_id = await self.ensure_default_group(context)
                task = asyncio.create_task(asyncio.to_thread(os.read, fd, _MAX_IMAGE_BYTES + 1))
                try:
                    body = await task
                except asyncio.CancelledError:
                    await asyncio.shield(task)
                    raise
                if len(body) != size or await asyncio.to_thread(os.read, fd, 1):
                    raise InvalidUpstreamResult("library upload source changed")
                object_key = f"refmedia/{uuid.uuid4().hex}{_EXTENSIONS[asset.mime_type]}"
                signed = tos_sign_put(
                    self._library_config().tos_access_key, self._library_config().tos_secret_key,
                    self._library_config().tos_bucket, self._library_config().tos_region, object_key, asset.mime_type, body,
                    now=self._now(),
                )
                try:
                    async with httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(60), follow_redirects=False, trust_env=False) as client:
                        response = await client.put(f"https://{signed['Host']}/{quote(object_key, safe='/')}", headers=signed, content=body)
                except httpx.HTTPError as error:
                    raise PortalUpstreamError("UPSTREAM_UNAVAILABLE", retryable=True) from error
                if response.status_code not in {200, 201}:
                    retryable = response.status_code in {408, 429} or response.status_code >= 500
                    raise PortalUpstreamError("UPSTREAM_UNAVAILABLE" if retryable else "REQUEST_REJECTED", retryable=retryable, status_code=response.status_code)
                public_url = tos_presigned_get_url(
                    self._library_config().tos_access_key, self._library_config().tos_secret_key,
                    self._library_config().tos_bucket, self._library_config().tos_region, object_key, now=self._now(),
                )
                upstream_id = await self._create_asset(context, group_id, public_url, source.name)
                status = await self._poll_status(context, upstream_id)
                return AssetRef(upstream_id, AssetKind.LIBRARY, status, asset.mime_type)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    async def get(self, context: RequestContext, asset_id: str) -> AssetRef:
        identifier = self._opaque(asset_id)
        response = await self._openapi_request(context, "GetAsset", {"Id": identifier, "ProjectName": self._library_config().project_name})
        data = self._json_object(response, "asset status")
        result = data.get("Result")
        if not isinstance(result, Mapping) or not isinstance(result.get("Id"), str) or result.get("Id") != identifier:
            raise InvalidUpstreamResult("asset library status is invalid")
        status = _STATUSES.get(result.get("Status"))
        if status is None:
            raise InvalidUpstreamResult("asset library status is invalid")
        return AssetRef(identifier, AssetKind.LIBRARY, status, "application/octet-stream")

    async def ensure_default_group(self, context: RequestContext) -> str:
        existing = self._group_id_getter()
        if existing is not None:
            if not isinstance(existing, str) or _GROUP_ID.fullmatch(existing) is None:
                raise InvalidUpstreamResult("asset library group is invalid")
            return existing
        response = await self._openapi_request(
            context, "CreateAssetGroup",
            {"Name": _DEFAULT_GROUP_NAME, "ProjectName": self._library_config().project_name, "GroupType": "AIGC"},
        )
        data = self._json_object(response, "asset group")
        result = data.get("Result")
        if not isinstance(result, Mapping):
            raise InvalidUpstreamResult("asset library group is invalid")
        identifier = result.get("Id") or result.get("GroupId")
        if not isinstance(identifier, str) or _GROUP_ID.fullmatch(identifier) is None:
            raise InvalidUpstreamResult("asset library group is invalid")
        self._group_id_setter(identifier)
        return identifier

    async def list_groups(self, context: RequestContext) -> tuple[dict[str, object], ...]:
        response = await self._openapi_request(
            context, "ListAssetGroups",
            {"Filter": {"GroupType": "AIGC"}, "PageNumber": 1, "PageSize": 50, "ProjectName": self._library_config().project_name},
        )
        data = self._json_object(response, "asset groups")
        result = data.get("Result")
        if not isinstance(result, Mapping) or not isinstance(result.get("Items"), list):
            raise InvalidUpstreamResult("asset library groups are invalid")
        groups: list[dict[str, object]] = []
        for item in result["Items"]:
            if not isinstance(item, Mapping):
                raise InvalidUpstreamResult("asset library groups are invalid")
            identifier = item.get("Id") or item.get("GroupId")
            name = item.get("Name")
            if not isinstance(identifier, str) or _GROUP_ID.fullmatch(identifier) is None or not isinstance(name, str) or not name:
                raise InvalidUpstreamResult("asset library groups are invalid")
            groups.append({"group_id": identifier, "name": name})
        return tuple(groups)

    async def _create_asset(self, context: RequestContext, group_id: str, public_url: str, source_name: str) -> str:
        name = Path(source_name).stem
        name = "".join(char for char in name if ord(char) >= 32 and ord(char) != 127)[:64] or "portrait"
        response = await self._openapi_request(
            context, "CreateAsset",
            {"GroupId": group_id, "URL": public_url, "AssetType": "Image", "ProjectName": self._library_config().project_name, "Name": name},
        )
        data = self._json_object(response, "asset")
        result = data.get("Result")
        if not isinstance(result, Mapping):
            raise InvalidUpstreamResult("asset library response is invalid")
        identifier = result.get("Id")
        if not isinstance(identifier, str) or _ARK_ASSET_ID.fullmatch(identifier) is None:
            raise InvalidUpstreamResult("asset library response is invalid")
        return identifier

    async def _poll_status(self, context: RequestContext, asset_id: str) -> AssetStatus:
        for attempt in range(self._get_asset_attempts):
            response = await self._openapi_request(context, "GetAsset", {"Id": asset_id, "ProjectName": self._library_config().project_name})
            data = self._json_object(response, "asset status")
            result = data.get("Result")
            if not isinstance(result, Mapping) or result.get("Id") != asset_id:
                raise InvalidUpstreamResult("asset library status is invalid")
            status = _STATUSES.get(result.get("Status"))
            if status is None:
                raise InvalidUpstreamResult("asset library status is invalid")
            if status is not AssetStatus.PROCESSING:
                return status
            if attempt + 1 < self._get_asset_attempts and self._get_asset_interval > 0:
                await asyncio.sleep(self._get_asset_interval)
        return AssetStatus.PROCESSING

    async def _openapi_request(self, context: RequestContext, action: str, payload: Mapping[str, object]) -> httpx.Response:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        query = f"Action={action}&Version={_VERSION}"
        authorization, headers = openapi_v4_sign(
            self._library_config().ark_access_key, self._library_config().ark_secret_key,
            "POST", _HOST, "/", query, body, now=self._now(),
        )
        headers["Authorization"] = authorization
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(30), follow_redirects=False, trust_env=False) as client:
                response = await client.post(f"https://{_HOST}/?{query}", headers=headers, content=body)
        except httpx.HTTPError as error:
            raise PortalUpstreamError("UPSTREAM_UNAVAILABLE", retryable=True) from error
        if response.status_code in {408, 429} or response.status_code >= 500:
            raise PortalUpstreamError("UPSTREAM_UNAVAILABLE", retryable=True, status_code=response.status_code)
        if response.status_code < 200 or response.status_code >= 300:
            raise PortalUpstreamError("REQUEST_REJECTED", retryable=False, status_code=response.status_code)
        return response

    @staticmethod
    def _json_object(response: httpx.Response, phase: str) -> Mapping:
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise InvalidUpstreamResult(f"asset library {phase} response is invalid")
        try:
            value = response.json()
        except (ValueError, TypeError) as error:
            raise InvalidUpstreamResult(f"asset library {phase} response is invalid") from error
        if not isinstance(value, Mapping):
            raise InvalidUpstreamResult(f"asset library {phase} response is invalid")
        return value

    @staticmethod
    def _opaque(value: str) -> str:
        if not isinstance(value, str) or not _OPAQUE.fullmatch(value):
            raise InvalidUpstreamResult("asset library identifier is invalid")
        return value

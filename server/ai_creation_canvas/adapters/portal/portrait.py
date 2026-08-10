"""Trusted portrait-asset to generic video capability adapter."""
from __future__ import annotations

import asyncio
import re
import os
import secrets
import stat
from dataclasses import dataclass
from typing import Mapping, AsyncIterator
from pathlib import Path
from urllib.parse import quote

import httpx

from ai_creation_canvas.adapters.portal.catalog import ServiceDeclaration, _safe_json
from ai_creation_canvas.adapters.portal.client import PortalClient
from ai_creation_canvas.domain.models import AssetRef, AssetStatus, JobRequest, JobState, ModelOperation, ModelSpec, RequestContext, UpstreamJob
from ai_creation_canvas.errors import ApiError, InvalidUpstreamResult, PortalUpstreamError

_OPAQUE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_STATUSES = {"Processing": AssetStatus.PROCESSING, "Active": AssetStatus.ACTIVE, "Failed": AssetStatus.FAILED}
_MIMES = {"image/png", "image/jpeg", "image/webp"}
_RESULTS_ROUTE = "api/virtual/results"

@dataclass(frozen=True, slots=True)
class PortraitDeclaration:
    service_id: str
    mount: str
    routes: Mapping[str, str] | None = None

    def __post_init__(self):
        if self.service_id != "portal-portrait": raise ValueError("portrait service ID is invalid")
        routes = self.routes or {"catalog":"api/config", "groups":"api/virtual/groups", "assets":"api/virtual/assets", "jobs":"api/virtual/jobs"}
        if set(routes) != {"catalog", "groups", "assets", "jobs"}:
            raise ValueError("portrait routes are invalid")
        for value in routes.values():
            if not isinstance(value, str) or not value or value.startswith("/") or "%" in value:
                raise ValueError("portrait route is invalid")
        object.__setattr__(self, "routes", dict(routes))

class PortalPortraitAdapter:
    requires_portal_cookie = True
    def __init__(self, declaration: PortraitDeclaration, client: PortalClient):
        self.service_id, self._declaration, self._client = declaration.service_id, declaration, client

    @staticmethod
    def _json_object(response: httpx.Response, phase: str) -> Mapping:
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise InvalidUpstreamResult(f"portrait {phase} response is invalid")
        try: value = response.json()
        except (ValueError, TypeError) as error: raise InvalidUpstreamResult(f"portrait {phase} response is invalid") from error
        if not isinstance(value, Mapping): raise InvalidUpstreamResult(f"portrait {phase} response is invalid")
        return value

    @staticmethod
    def _opaque(value: object) -> str:
        if not isinstance(value, str) or not _OPAQUE.fullmatch(value):
            raise InvalidUpstreamResult("portrait upstream identifier is invalid")
        return value

    @staticmethod
    def _asset(payload: object) -> AssetRef:
        if not isinstance(payload, Mapping): raise InvalidUpstreamResult("portrait response is invalid")
        identifier = PortalPortraitAdapter._opaque(payload.get("id"))
        status = _STATUSES.get(payload.get("status"))
        mime = payload.get("mime_type", "application/octet-stream")
        if status is None or not isinstance(mime, str) or not mime:
            raise InvalidUpstreamResult("portrait response is invalid")
        return AssetRef(identifier, "portrait", status, mime)

    async def list_models(self, context: RequestContext, *, cookie_header: str | None = None):
        return await self._models(context, cookie_header)
    async def list_models_with_cookie(self, context: RequestContext, cookie_header: str):
        return await self._models(context, cookie_header)
    async def _models(self, context, cookie):
        response = await self._client.request(context, "GET", self._declaration.routes["catalog"], mount=self._declaration.mount, cookie_header=cookie)
        try:
            payload = self._json_object(response, "catalog"); items = payload["models"]
            if response.status_code != 200 or not isinstance(items, list): raise ValueError
            output=[]
            for item in items:
                if not isinstance(item, Mapping) or not isinstance(item.get("id"), str) or not isinstance(item.get("display_name"), str): raise ValueError
                ops = tuple(ModelOperation(op) for op in item.get("operations", ()))
                if ModelOperation.VIDEO_IMAGE_TO_VIDEO not in ops or not set(ops).issubset({ModelOperation.VIDEO_IMAGE_TO_VIDEO}): raise ValueError
                media = item.get("input_media", [])
                schema = item.get("parameter_schema", {})
                if not isinstance(media, list) or not {"text", "image"}.issubset(set(media)) or not isinstance(schema, Mapping) or not _safe_json(schema): raise ValueError
                output.append(ModelSpec(item["id"], self.service_id, item["display_name"], ops, tuple(media), schema, "portrait"))
            return tuple(output)
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError("portrait model configuration is invalid") from error

    async def upload(self, context: RequestContext, asset: AssetRef):
        raise ValueError("portrait upload requires request-scoped cookie and bytes")
    async def upload_with_cookie(self, context, asset, source: Path, size: int, cookie_header: str):
        if asset.kind.value != "portrait" or asset.mime_type not in _MIMES or not isinstance(source, Path) or not isinstance(size, int) or size < 1: raise ValueError("portrait upload is invalid")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(source, flags)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != size or size > 10 * 1024 * 1024:
                os.close(fd); raise ValueError("portrait upload source is invalid")
        except OSError as error:
            raise ValueError("portrait upload source is invalid") from error
        try:
            group = await self._client.request(context, "POST", self._declaration.routes["groups"], mount=self._declaration.mount, cookie_header=cookie_header, json={})
            if group.status_code not in {200, 201}: raise PortalUpstreamError(retryable=group.status_code in {408,429} or group.status_code >= 500, status_code=group.status_code)
            group_payload = self._json_object(group, "group")
            group_id = self._opaque(group_payload.get("id"))
            boundary = secrets.token_hex(24)
            prefix, suffix = self._multipart_edges(boundary, asset.mime_type, group_id)
            response = await self._client.request(context, "POST", self._declaration.routes["assets"], mount=self._declaration.mount, cookie_header=cookie_header, content=self._multipart(fd, size, prefix, suffix), extra_headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(prefix) + size + len(suffix))})
            if response.status_code not in {200, 201, 202}: raise PortalUpstreamError(retryable=response.status_code in {408,429} or response.status_code >= 500, status_code=response.status_code)
            return self._asset(self._json_object(response, "asset"))
        except asyncio.CancelledError: raise
        except httpx.HTTPError as error: raise PortalUpstreamError(retryable=True) from error
        finally:
            try: os.close(fd)
            except OSError: pass

    async def get(self, context, asset_id): return await self._get(context, asset_id, None)
    async def get_with_cookie(self, context, asset_id, cookie_header): return await self._get(context, asset_id, cookie_header)
    async def _get(self, context, asset_id, cookie):
        identifier = self._opaque(asset_id)
        response = await self._client.request(context, "GET", f"{self._declaration.routes['assets']}/{quote(identifier, safe='')}", mount=self._declaration.mount, cookie_header=cookie)
        if response.status_code != 200: raise PortalUpstreamError(retryable=response.status_code in {408,429} or response.status_code >= 500, status_code=response.status_code)
        return self._asset(self._json_object(response, "asset"))

    async def submit(self, context, request): return await self._submit(context, request, None)
    async def submit_with_cookie(self, context, request, cookie_header): return await self._submit(context, request, cookie_header)
    async def _submit(self, context, request, cookie):
        if request.operation is not ModelOperation.VIDEO_IMAGE_TO_VIDEO or len(request.asset_ids) != 1: raise ValueError("portrait video request is invalid")
        payload = {"operation": request.operation.value, "model_id": request.model_id, "prompt": request.prompt, "params": dict(request.params), "asset_ids": [self._opaque(request.asset_ids[0])], "idempotency_key": request.idempotency_key}
        response = await self._client.request(context, "POST", self._declaration.routes["jobs"], mount=self._declaration.mount, cookie_header=cookie, json=payload)
        if response.status_code not in {200,201,202}: raise PortalUpstreamError(retryable=response.status_code in {408,429} or response.status_code >= 500, status_code=response.status_code)
        data=self._json_object(response, "job")
        identifier=self._opaque(data.get("id")); status=data.get("status", "queued")
        try: return UpstreamJob(self.service_id, identifier, JobState(identifier, status))
        except ValueError as error: raise InvalidUpstreamResult("portrait job response is invalid") from error
    async def poll(self, context, upstream_job_id): return await self._poll(context, upstream_job_id, None)
    async def poll_with_cookie(self, context, upstream_job_id, cookie_header): return await self._poll(context, upstream_job_id, cookie_header)
    async def _poll(self, context, upstream_job_id, cookie):
        identifier=self._opaque(upstream_job_id); response=await self._client.request(context,"GET",f"{self._declaration.routes['jobs']}/{quote(identifier,safe='')}",mount=self._declaration.mount,cookie_header=cookie)
        if response.status_code != 200: raise PortalUpstreamError(retryable=response.status_code in {408,429} or response.status_code >= 500,status_code=response.status_code)
        data=self._json_object(response, "job")
        status=data.get("status")
        if status == "failed": return JobState(identifier,"failed",error=ApiError("TASK_FAILED","The generation task failed.",False,context.request_id,"generation"))
        try:
            result = None
            if status == "succeeded":
                result = AssetRef(self._opaque(data.get("result_ref")), "reference", "active", "application/octet-stream")
            return JobState(identifier, status, result=result)
        except ValueError as error: raise InvalidUpstreamResult("portrait job response is invalid") from error

    async def open_result(self, context, result_id, *, cookie_header, range_header=None, head=False):
        """Open only opaque Portal-owned portrait result IDs for the common proxy."""
        identifier = self._opaque(result_id)
        if not isinstance(cookie_header, str) or not cookie_header:
            raise ValueError("Cookie header is required")
        headers = {"Range": range_header} if range_header else None
        return await self._client.open_stream(
            context,
            "HEAD" if head else "GET",
            f"{_RESULTS_ROUTE}/{quote(identifier, safe='')}",
            mount=self._declaration.mount,
            cookie_header=cookie_header,
            headers=headers,
        )

    @staticmethod
    def _multipart_edges(boundary: str, mime: str, group_id: str) -> tuple[bytes, bytes]:
        prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"group_id\"\r\n\r\n{group_id}\r\n"
                  f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"upload.bin\"\r\nContent-Type: {mime}\r\n\r\n").encode()
        return prefix, f"\r\n--{boundary}--\r\n".encode()

    @staticmethod
    async def _multipart(fd: int, size: int, prefix: bytes, suffix: bytes) -> AsyncIterator[bytes]:
        yield prefix
        remaining = size
        while remaining:
            chunk = await PortalPortraitAdapter._read_fd_safely(fd, min(65536, remaining))
            if not chunk: raise InvalidUpstreamResult("portrait upload source changed")
            remaining -= len(chunk)
            yield chunk
        if await PortalPortraitAdapter._read_fd_safely(fd, 1): raise InvalidUpstreamResult("portrait upload source changed")
        yield suffix

    @staticmethod
    async def _read_fd_safely(fd: int, amount: int) -> bytes:
        task = asyncio.create_task(asyncio.to_thread(os.read, fd, amount))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError as original:
            # Do not release the FD while its worker may still read it: a reused
            # descriptor could otherwise expose a different file to that worker.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if task.done():
                try:
                    task.result()
                except Exception:
                    pass
            raise original

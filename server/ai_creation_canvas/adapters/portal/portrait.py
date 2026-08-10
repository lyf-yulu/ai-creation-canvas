"""Trusted portrait-asset to generic video capability adapter."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import quote

import httpx

from ai_creation_canvas.adapters.portal.catalog import ServiceDeclaration, _safe_json
from ai_creation_canvas.adapters.portal.client import PortalClient
from ai_creation_canvas.domain.models import AssetRef, AssetStatus, JobRequest, JobState, ModelOperation, ModelSpec, RequestContext, UpstreamJob
from ai_creation_canvas.errors import ApiError, InvalidUpstreamResult, PortalUpstreamError

_OPAQUE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_STATUSES = {"Processing": AssetStatus.PROCESSING, "Active": AssetStatus.ACTIVE, "Failed": AssetStatus.FAILED}

@dataclass(frozen=True, slots=True)
class PortraitDeclaration:
    service_id: str
    mount: str
    config_path: str = "api/config"
    groups_path: str = "virtual/groups"
    assets_path: str = "virtual/assets"
    jobs_path: str = "virtual/jobs"

    def __post_init__(self):
        if self.service_id != "portal-portrait": raise ValueError("portrait service ID is invalid")
        for value in (self.config_path, self.groups_path, self.assets_path, self.jobs_path):
            if not isinstance(value, str) or not value or "/" not in value or value.startswith("/") or "%" in value:
                raise ValueError("portrait route is invalid")

class PortalPortraitAdapter:
    requires_portal_cookie = True
    def __init__(self, declaration: PortraitDeclaration, client: PortalClient):
        self.service_id, self._declaration, self._client = declaration.service_id, declaration, client

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
        response = await self._client.request(context, "GET", self._declaration.config_path, mount=self._declaration.mount, cookie_header=cookie)
        try:
            payload = response.json(); items = payload["models"]
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
    async def upload_with_cookie(self, context, asset, content: bytes, filename: str, cookie_header: str):
        if asset.kind.value != "portrait" or not isinstance(content, bytes) or not content or not isinstance(filename, str): raise ValueError("portrait upload is invalid")
        try:
            group = await self._client.request(context, "POST", self._declaration.groups_path, mount=self._declaration.mount, cookie_header=cookie_header, json={})
            if group.status_code not in {200, 201}: raise PortalUpstreamError(retryable=group.status_code >= 500, status_code=group.status_code)
            group_id = self._opaque(group.json().get("id"))
            response = await self._client.request(context, "POST", self._declaration.assets_path, mount=self._declaration.mount, cookie_header=cookie_header, data={"group_id": group_id}, files={"file": (filename, content, asset.mime_type)})
            if response.status_code not in {200, 201, 202}: raise PortalUpstreamError(retryable=response.status_code >= 500, status_code=response.status_code)
            return self._asset(response.json())
        except asyncio.CancelledError: raise
        except httpx.HTTPError as error: raise PortalUpstreamError(retryable=True) from error

    async def get(self, context, asset_id): return await self._get(context, asset_id, None)
    async def get_with_cookie(self, context, asset_id, cookie_header): return await self._get(context, asset_id, cookie_header)
    async def _get(self, context, asset_id, cookie):
        identifier = self._opaque(asset_id)
        response = await self._client.request(context, "GET", f"{self._declaration.assets_path}/{quote(identifier, safe='')}", mount=self._declaration.mount, cookie_header=cookie)
        if response.status_code != 200: raise PortalUpstreamError(retryable=response.status_code >= 500, status_code=response.status_code)
        return self._asset(response.json())

    async def submit(self, context, request): return await self._submit(context, request, None)
    async def submit_with_cookie(self, context, request, cookie_header): return await self._submit(context, request, cookie_header)
    async def _submit(self, context, request, cookie):
        if request.operation is not ModelOperation.VIDEO_IMAGE_TO_VIDEO or len(request.asset_ids) != 1: raise ValueError("portrait video request is invalid")
        payload = {"operation": request.operation.value, "model_id": request.model_id, "prompt": request.prompt, "params": dict(request.params), "asset_ids": [self._opaque(request.asset_ids[0])], "idempotency_key": request.idempotency_key}
        response = await self._client.request(context, "POST", self._declaration.jobs_path, mount=self._declaration.mount, cookie_header=cookie, json=payload)
        if response.status_code not in {200,201,202}: raise PortalUpstreamError(retryable=response.status_code >= 500, status_code=response.status_code)
        data=response.json(); identifier=self._opaque(data.get("id")); status=data.get("status", "queued")
        try: return UpstreamJob(self.service_id, identifier, JobState(identifier, status))
        except ValueError as error: raise InvalidUpstreamResult("portrait job response is invalid") from error
    async def poll(self, context, upstream_job_id): return await self._poll(context, upstream_job_id, None)
    async def poll_with_cookie(self, context, upstream_job_id, cookie_header): return await self._poll(context, upstream_job_id, cookie_header)
    async def _poll(self, context, upstream_job_id, cookie):
        identifier=self._opaque(upstream_job_id); response=await self._client.request(context,"GET",f"{self._declaration.jobs_path}/{quote(identifier,safe='')}",mount=self._declaration.mount,cookie_header=cookie)
        if response.status_code != 200: raise PortalUpstreamError(retryable=response.status_code >= 500,status_code=response.status_code)
        data=response.json(); status=data.get("status")
        if status == "failed": return JobState(identifier,"failed",error=ApiError("TASK_FAILED","The generation task failed.",False,context.request_id,"generation"))
        try: return JobState(identifier,status)
        except ValueError as error: raise InvalidUpstreamResult("portrait job response is invalid") from error

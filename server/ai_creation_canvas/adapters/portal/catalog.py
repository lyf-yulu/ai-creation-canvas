"""Portal-backed model catalog adapters with strict data-only configuration."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import httpx
import re
from typing import Any, Mapping, Protocol, runtime_checkable
from urllib.parse import quote

from ai_creation_canvas.adapters.portal.client import PortalClient
from ai_creation_canvas.domain.models import AssetRef, JobRequest, JobState, ModelOperation, ModelSpec, RequestContext, UpstreamJob
from ai_creation_canvas.errors import ApiError, InvalidUpstreamResult, PortalUpstreamError
from ai_creation_canvas.domain.registry import AdapterRegistry


_MAX_MODELS = 64
_MAX_CONFIG_BYTES = 512 * 1024
_MAX_SCHEMA_DEPTH = 8
_MAX_SCHEMA_ITEMS = 128
_MAX_TEXT_LENGTH = 4096
_RESULT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _safe_json(value: object, *, depth: int = 0) -> bool:
    """Bound untrusted schema complexity before freezing it into a domain value."""
    if depth > _MAX_SCHEMA_DEPTH:
        return False
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        return len(value) <= _MAX_TEXT_LENGTH
    if isinstance(value, Mapping):
        return (
            len(value) <= _MAX_SCHEMA_ITEMS
            and all(isinstance(key, str) and len(key) <= 128 and _safe_json(item, depth=depth + 1) for key, item in value.items())
        )
    if isinstance(value, list):
        return len(value) <= _MAX_SCHEMA_ITEMS and all(_safe_json(item, depth=depth + 1) for item in value)
    return False


@dataclass(frozen=True, slots=True)
class CatalogResult:
    models: tuple[ModelSpec, ...]
    diagnostics: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class ServiceDeclaration:
    service_id: str
    mount: str
    capability: str
    operations: tuple[ModelOperation | str, ...]
    contract_id: str | None = None
    routes: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.service_id, str) or not self.service_id.strip():
            raise ValueError("service_id must be a non-empty identifier")
        if not isinstance(self.mount, str) or not self.mount:
            raise ValueError("mount must be non-empty")
        if not isinstance(self.capability, str) or not self.capability.strip():
            raise ValueError("capability must be non-empty")
        operations = tuple(ModelOperation(item) for item in self.operations)
        if not operations:
            raise ValueError("operations must not be empty")
        object.__setattr__(self, "operations", operations)
        if self.capability == "portrait_asset":
            if self.contract_id != "portal-virtual-v1" or not isinstance(self.routes, Mapping) or set(self.routes) != {"catalog", "groups", "assets", "jobs"}:
                raise ValueError("portrait declaration requires the supported contract")
        elif self.contract_id is not None or self.routes is not None:
            raise ValueError("non-portrait declarations cannot define a contract")


@runtime_checkable
class PortalCookieGenerationPort(Protocol):
    """Optional trusted adapter capability for a request-scoped Portal session."""

    async def list_models_with_cookie(self, context: RequestContext, cookie_header: str) -> tuple[ModelSpec, ...]: ...


class PortalJobsAdapter:
    """Maps a trusted service's data-only `/api/config` response into ModelSpec values."""

    requires_portal_cookie = True
    requires_request_scoped_polling = True

    def __init__(self, declaration: ServiceDeclaration, client: PortalClient) -> None:
        self.service_id = declaration.service_id
        self._declaration = declaration
        self._client = client

    async def list_models(self, context: RequestContext, *, cookie_header: str | None = None) -> tuple[ModelSpec, ...]:
        if cookie_header is None:
            return await self._list_models(context, None)
        return await self.list_models_with_cookie(context, cookie_header)

    async def list_models_with_cookie(self, context: RequestContext, cookie_header: str) -> tuple[ModelSpec, ...]:
        return await self._list_models(context, cookie_header)

    async def _list_models(self, context: RequestContext, cookie_header: str | None) -> tuple[ModelSpec, ...]:
        response = await self._client.request(
            context, "GET", "api/config", mount=self._declaration.mount, cookie_header=cookie_header
        )
        if response.status_code != 200 or len(response.content) > _MAX_CONFIG_BYTES:
            raise ValueError("Portal model configuration is unavailable")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("Portal model configuration is invalid")
        try:
            payload = response.json()
        except ValueError as error:
            raise ValueError("Portal model configuration is invalid") from error
        return self._models_from_payload(payload)

    def _models_from_payload(self, payload: object) -> tuple[ModelSpec, ...]:
        if not isinstance(payload, Mapping) or set(payload).isdisjoint({"models"}):
            raise ValueError("Portal model configuration is invalid")
        items = payload.get("models")
        if not isinstance(items, list) or len(items) > _MAX_MODELS:
            raise ValueError("Portal model configuration is invalid")
        models: list[ModelSpec] = []
        for item in items:
            if not isinstance(item, Mapping):
                raise ValueError("Portal model configuration is invalid")
            model_id = item.get("id")
            display_name = item.get("display_name")
            operations = item.get("operations")
            input_media = item.get("input_media", [])
            schema = item.get("parameter_schema", {})
            asset_kind = item.get("requires_asset_kind")
            if (
                not isinstance(model_id, str)
                or not isinstance(display_name, str)
                or not isinstance(operations, list)
                or not isinstance(input_media, list)
                or not isinstance(schema, Mapping)
                or asset_kind is not None and not isinstance(asset_kind, str)
                or not _safe_json(schema)
            ):
                raise ValueError("Portal model configuration is invalid")
            parsed_ops = tuple(ModelOperation(operation) for operation in operations)
            if not set(parsed_ops).issubset(set(self._declaration.operations)):
                raise ValueError("Portal model configuration declares an unsupported operation")
            models.append(ModelSpec(model_id, self.service_id, display_name, parsed_ops, tuple(input_media), schema, asset_kind))
        return tuple(models)

    async def submit(self, context: RequestContext, request: JobRequest) -> UpstreamJob:
        return await self._submit(context, request, None)

    async def submit_with_cookie(self, context: RequestContext, request: JobRequest, cookie_header: str) -> UpstreamJob:
        return await self._submit(context, request, cookie_header)

    async def _submit(self, context: RequestContext, request: JobRequest, cookie_header: str | None) -> UpstreamJob:
        try:
            response = await self._client.request(context, "POST", "api/jobs", mount=self._declaration.mount, cookie_header=cookie_header, json={"operation": request.operation.value, "model_id": request.model_id, "prompt": request.prompt, "params": dict(request.params), "asset_ids": list(request.asset_ids), "idempotency_key": request.idempotency_key})
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as error:
            raise PortalUpstreamError(retryable=True) from error
        if response.status_code not in {200, 201, 202}:
            if 400 <= response.status_code < 500:
                raise PortalUpstreamError(retryable=response.status_code in {408, 429}, status_code=response.status_code)
            raise PortalUpstreamError(retryable=True, status_code=response.status_code)
        try:
            payload = response.json()
            upstream_id = payload["id"]
            status = payload.get("status", "queued")
            if not isinstance(upstream_id, str): raise ValueError
            state = JobState(upstream_id, status)
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError("generation submission is invalid") from error
        return UpstreamJob(self.service_id, upstream_id, state)

    async def poll(self, context: RequestContext, upstream_job_id: str) -> JobState:
        return await self._poll(context, upstream_job_id, None)

    async def poll_with_cookie(self, context: RequestContext, upstream_job_id: str, cookie_header: str) -> JobState:
        return await self._poll(context, upstream_job_id, cookie_header)

    async def _poll(self, context: RequestContext, upstream_job_id: str, cookie_header: str | None) -> JobState:
        try:
            response = await self._client.request(context, "GET", f"api/jobs/{upstream_job_id}", mount=self._declaration.mount, cookie_header=cookie_header)
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as error:
            raise PortalUpstreamError(retryable=True) from error
        if response.status_code != 200:
            if 400 <= response.status_code < 500:
                raise PortalUpstreamError(retryable=response.status_code in {408, 429}, status_code=response.status_code)
            raise PortalUpstreamError(retryable=True, status_code=response.status_code)
        try:
            payload = response.json(); status = payload["status"]
            result = payload.get("result_ref")
            if status == "succeeded" and (not isinstance(result, str) or not _RESULT_ID.fullmatch(result)):
                raise InvalidUpstreamResult("provider success result is invalid")
            ref = AssetRef(result, "reference", "active", "application/octet-stream") if status == "succeeded" else None
            if status == "failed":
                return JobState(upstream_job_id, status, error=ApiError("TASK_FAILED", "The generation task failed.", False, context.request_id, "generation"))
            return JobState(upstream_job_id, status, result=ref)
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError("generation poll is invalid") from error

    async def fetch_result(self, context: RequestContext, upstream_job_id: str, result_ref: str, cookie_header: str | None = None) -> tuple[bytes, str]:
        response = await self._client.request(context, "GET", f"api/results/{upstream_job_id}/{result_ref}", mount=self._declaration.mount, cookie_header=cookie_header)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if response.status_code != 200 or content_type not in {"image/png", "image/jpeg", "image/webp", "video/mp4", "video/webm"}:
            raise ValueError("generation result is unavailable")
        return response.content, content_type

    async def open_result(self, context: RequestContext, result_id: str, *, cookie_header: str, range_header: str | None = None, head: bool = False):
        if not cookie_header: raise ValueError("Cookie header is required")
        headers = {"Range": range_header} if range_header else None
        return await self._client.open_stream(context, "HEAD" if head else "GET", f"api/results/{quote(result_id, safe='')}", mount=self._declaration.mount, cookie_header=cookie_header, headers=headers)


def is_trusted_request_scoped_adapter(adapter: object) -> bool:
    """Accept request Cookie polling only for the two code-owned Portal adapters."""
    from ai_creation_canvas.adapters.portal.portrait import PortalPortraitAdapter

    return type(adapter) in {PortalJobsAdapter, PortalPortraitAdapter}


class ModelCatalog:
    def __init__(self, registry: AdapterRegistry) -> None:
        self._registry = registry

    @property
    def requires_portal_cookie(self) -> bool:
        return any(getattr(adapter, "requires_portal_cookie", False) for adapter in self._registry.generation_adapters())

    async def list_models(self, context: RequestContext, *, cookie_header: str | None = None) -> CatalogResult:
        models: list[ModelSpec] = []
        diagnostics: list[dict[str, str]] = []
        adapters = self._registry.generation_adapters()
        async def load(adapter):
            try:
                if cookie_header is not None and isinstance(adapter, PortalCookieGenerationPort):
                    adapter_models = await adapter.list_models_with_cookie(context, cookie_header)
                else:
                    adapter_models = await adapter.list_models(context)
                if any(model.service_id != adapter.service_id for model in adapter_models):
                    raise ValueError("adapter returned a mismatched service")
            except Exception:
                return adapter, None
            return adapter, adapter_models
        results = await asyncio.gather(*(load(adapter) for adapter in adapters), return_exceptions=True)
        for adapter, result in zip(adapters, results, strict=True):
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                diagnostics.append({"service_id": adapter.service_id, "code": "MODEL_CATALOG_UNAVAILABLE"})
            elif result[1] is None:
                diagnostics.append({"service_id": adapter.service_id, "code": "MODEL_CATALOG_UNAVAILABLE"})
            else:
                models.extend(result[1])
        duplicate_ids = {model.model_id for model in models if sum(other.model_id == model.model_id for other in models) > 1}
        if duplicate_ids:
            impacted = sorted({model.service_id for model in models if model.model_id in duplicate_ids})
            diagnostics.extend({"service_id": service_id, "code": "DUPLICATE_MODEL_ID"} for service_id in impacted)
            models = [model for model in models if model.model_id not in duplicate_ids]
        return CatalogResult(
            models=tuple(sorted(models, key=lambda item: (item.model_id, item.service_id))),
            diagnostics=tuple(sorted(diagnostics, key=lambda item: (item["service_id"], item["code"]))),
        )

    async def resolve_model(self, context: RequestContext, model_id: str, *, cookie_header: str | None = None) -> ModelSpec:
        """Resolve local services first; only ask protected services when needed."""
        adapters = self._registry.generation_adapters()
        local = tuple(adapter for adapter in adapters if not getattr(adapter, "requires_portal_cookie", False))
        protected = tuple(adapter for adapter in adapters if getattr(adapter, "requires_portal_cookie", False))

        async def models_for(items, cookie):
            result: list[ModelSpec] = []
            for adapter in items:
                try:
                    if cookie is not None and isinstance(adapter, PortalCookieGenerationPort):
                        models = await adapter.list_models_with_cookie(context, cookie)
                    else:
                        models = await adapter.list_models(context)
                    result.extend(model for model in models if model.service_id == adapter.service_id)
                except Exception:
                    continue
            return [model for model in result if model.model_id == model_id]

        matches = await models_for(local, None)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError("model is ambiguous")
        if protected and not cookie_header:
            from ai_creation_canvas.adapters.portal.identity import AuthRequired
            raise AuthRequired(context.request_id)
        matches = await models_for(protected, cookie_header)
        if len(matches) != 1:
            raise ValueError("model is unavailable")
        return matches[0]

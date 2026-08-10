"""Server-side model assignment boundary for standalone users."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ai_creation_canvas.adapters.portal.catalog import CatalogResult
from ai_creation_canvas.domain.models import ModelSpec, PortalRole, RequestContext
from ai_creation_canvas.storage.sqlite import CanvasStore


@runtime_checkable
class ModelCatalogPort(Protocol):
    async def list_models(self, context: RequestContext, *, cookie_header: str | None = None) -> CatalogResult: ...
    async def resolve_model(self, context: RequestContext, model_id: str, *, cookie_header: str | None = None) -> ModelSpec: ...


class AssignedModelCatalog:
    def __init__(self, catalog: ModelCatalogPort, store: CanvasStore) -> None:
        if not isinstance(catalog, ModelCatalogPort):
            raise TypeError("catalog does not implement ModelCatalogPort")
        self._catalog = catalog
        self._store = store

    async def list_models(self, context: RequestContext, *, cookie_header: str | None = None) -> CatalogResult:
        result = await self._catalog.list_models(context, cookie_header=cookie_header)
        if context.user.role is PortalRole.ADMIN:
            return result
        assigned = frozenset(self._store.assigned_models(context.user.user_id))
        return CatalogResult(tuple(model for model in result.models if model.model_id in assigned), result.diagnostics)

    async def resolve_model(self, context: RequestContext, model_id: str, *, cookie_header: str | None = None) -> ModelSpec:
        if context.user.role is not PortalRole.ADMIN and model_id not in self._store.assigned_models(context.user.user_id):
            raise ValueError("model is unavailable")
        return await self._catalog.resolve_model(context, model_id, cookie_header=cookie_header)

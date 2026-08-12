"""Server-side model assignment boundary for standalone users."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
from typing import Protocol, runtime_checkable

from ai_creation_canvas.adapters.portal.catalog import CatalogResult
from ai_creation_canvas.domain.models import ModelSpec, PortalRole, RequestContext
from ai_creation_canvas.storage.sqlite import CanvasStore
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.model_registry import GovernedModelDefinition, ProviderDefinition
from ai_creation_canvas.adapters.factory import AdapterFactory


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
        assigned = frozenset((*self._store.assigned_models(context.user.user_id), *self._store.governed_assigned_models(context.user.user_id)))
        return CatalogResult(tuple(model for model in result.models if model.model_id in assigned), result.diagnostics)

    async def resolve_model(self, context: RequestContext, model_id: str, *, cookie_header: str | None = None) -> ModelSpec:
        if context.user.role is not PortalRole.ADMIN and model_id not in {*self._store.assigned_models(context.user.user_id), *self._store.governed_assigned_models(context.user.user_id)}:
            raise ValueError("model is unavailable")
        return await self._catalog.resolve_model(context, model_id, cookie_header=cookie_header)

    def model_binding(self, model_id: str):
        resolver = getattr(self._catalog, "model_binding", None)
        return resolver(model_id) if callable(resolver) else None


@dataclass(frozen=True, slots=True)
class GovernedModelBinding:
    provider: ProviderDefinition
    model: GovernedModelDefinition


class GovernedModelCatalog:
    """Refresh trusted adapters from SQL definitions before using the base catalog."""
    def __init__(self, base: ModelCatalogPort, store: CanvasStore, registry: AdapterRegistry, factory: AdapterFactory) -> None:
        self._base, self._store, self._registry, self._factory = base, store, registry, factory
        self._registered: set[str] = set()
        self._bindings: dict[str, GovernedModelBinding] = {}
        self._lock = asyncio.Lock()

    async def _refresh(self) -> None:
        async with self._lock:
            for service_id in self._registered:
                self._registry.unregister_generation(service_id)
            self._registered.clear()
            self._bindings.clear()
            providers = {item.provider_id: item for item in self._store.list_provider_definitions() if item.enabled}
            grouped: dict[str, list[GovernedModelDefinition]] = {}
            for model in self._store.list_model_definitions(enabled_only=True):
                if model.provider_id in providers:
                    grouped.setdefault(model.provider_id, []).append(model)
            for provider_id, models in sorted(grouped.items()):
                provider = providers[provider_id]
                try:
                    adapter = self._factory.build(provider, tuple(models))
                    self._registry.replace_generation(adapter)
                except ValueError:
                    continue
                self._registered.add(adapter.service_id)
                self._bindings.update({model.model_id: GovernedModelBinding(provider, model) for model in models})

    async def list_models(self, context: RequestContext, *, cookie_header: str | None = None) -> CatalogResult:
        await self._refresh()
        return await self._base.list_models(context, cookie_header=cookie_header)

    async def resolve_model(self, context: RequestContext, model_id: str, *, cookie_header: str | None = None) -> ModelSpec:
        await self._refresh()
        return await self._base.resolve_model(context, model_id, cookie_header=cookie_header)

    def model_binding(self, model_id: str) -> GovernedModelBinding | None:
        return self._bindings.get(model_id)

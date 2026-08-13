"""Server-side model assignment boundary for standalone users."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
from threading import Lock
from typing import Callable, Mapping, Protocol, runtime_checkable

from ai_creation_canvas.adapters.portal.catalog import CatalogResult
from ai_creation_canvas.domain.models import ModelSpec, PortalRole, RequestContext
from ai_creation_canvas.storage.sqlite import CanvasStore
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.model_registry import GovernedModelDefinition, ProviderDefinition
from ai_creation_canvas.adapters.factory import AdapterFactory
from ai_creation_canvas.credential_pools import CredentialPool
from ai_creation_canvas.model_routing import LogicalModelDefinition, validate_route_model, validate_route_pool
from ai_creation_canvas.routing import RouteSelector


class ProviderSubmissionBudgetExhausted(RuntimeError):
    """Raised before provider I/O when the acceptance budget is exhausted."""


class ProviderSubmissionBudget:
    """Process-local atomic ceiling for provider submission attempts."""

    def __init__(self, maximum: int) -> None:
        if type(maximum) is not int or not 1 <= maximum <= 20:
            raise ValueError("provider submission budget must be between one and twenty")
        self._maximum = maximum
        self._used = 0
        self._lock = Lock()

    def consume(self) -> int:
        with self._lock:
            if self._used >= self._maximum:
                raise ProviderSubmissionBudgetExhausted("provider submission budget exhausted")
            self._used += 1
            return self._used

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return self._maximum - self._used


@dataclass(frozen=True, slots=True, repr=False)
class ManagedRoutingRuntime:
    """Explicit app-owned boundary for managed logical-model routing."""

    store: CanvasStore
    pool_snapshot: Callable[[], Mapping[str, CredentialPool]]
    selector: RouteSelector
    coordinator: object
    adapter_factory: object
    submission_budget: ProviderSubmissionBudget | None = None

    def __post_init__(self) -> None:
        if (
            not callable(self.pool_snapshot)
            or not isinstance(self.selector, RouteSelector)
            or self.submission_budget is not None
            and not isinstance(self.submission_budget, ProviderSubmissionBudget)
        ):
            raise ValueError("managed routing runtime is invalid")

    def __repr__(self) -> str:
        return "ManagedRoutingRuntime()"

    def is_managed(self, model_id: str) -> bool:
        return self.store.logical_model(model_id) is not None

    def logical_model(self, model_id: str) -> LogicalModelDefinition | None:
        model = self.store.logical_model(model_id)
        return model if isinstance(model, LogicalModelDefinition) else None

    def pools(self) -> Mapping[str, CredentialPool]:
        pools = self.pool_snapshot()
        if not isinstance(pools, Mapping):
            raise ValueError("credential pool snapshot is unavailable")
        return pools

    def has_healthy_route(self, model: LogicalModelDefinition) -> bool:
        pools = self.pools()
        providers = {item.provider_id: item for item in self.store.list_provider_definitions() if item.enabled}
        for route in self.store.list_model_routes(model_id=model.model_id, include_archived=False):
            pool = pools.get(route.credential_pool_ref)
            provider = providers.get(route.provider_id)
            if not route.enabled or provider is None or provider.adapter_type != route.adapter_type or not isinstance(pool, CredentialPool) or not pool.keys:
                continue
            if not self.selector.accepts_trusted_route(route, model):
                continue
            try:
                validate_route_model(route, model)
                validate_route_pool(route, pool)
            except ValueError:
                continue
            return True
        return False


def _logical_model_spec(model: LogicalModelDefinition) -> ModelSpec:
    contracts = model.operation_contracts
    first = contracts[0]
    if any(
        contract.input_ports != first.input_ports
        or dict(contract.parameter_schema) != dict(first.parameter_schema)
        for contract in contracts[1:]
    ):
        raise ValueError("logical model public contract is unavailable")
    return ModelSpec(
        model.model_id,
        model.model_id,
        model.display_name,
        tuple(contract.operation for contract in contracts),
        tuple(dict.fromkeys(port.media_type for port in first.input_ports)),
        first.parameter_schema,
        None,
        first.input_ports,
        {},
    )


class LogicalModelCatalog:
    """Overlay managed logical models without exposing their routes or credentials."""

    def __init__(self, base: ModelCatalogPort, store: CanvasStore, runtime: ManagedRoutingRuntime) -> None:
        if not isinstance(base, ModelCatalogPort) or runtime.store is not store:
            raise ValueError("logical model catalog dependencies are invalid")
        self._base, self._store, self._runtime = base, store, runtime

    async def list_models(self, context: RequestContext, *, cookie_header: str | None = None) -> CatalogResult:
        base = await self._base.list_models(context, cookie_header=cookie_header)
        all_logical_ids = {model.model_id for model in self._store.list_logical_models()}
        models = [item for item in base.models if item.model_id not in all_logical_ids]
        assigned = None if context.user.role is PortalRole.ADMIN else frozenset(self._store.governed_assigned_models(context.user.user_id))
        for logical in self._store.list_logical_models(include_archived=False):
            if assigned is not None and logical.model_id not in assigned:
                continue
            if not logical.enabled or not self._runtime.has_healthy_route(logical):
                continue
            try:
                models.append(_logical_model_spec(logical))
            except ValueError:
                continue
        models.sort(key=lambda item: item.model_id)
        return CatalogResult(tuple(models), base.diagnostics)

    async def resolve_model(self, context: RequestContext, model_id: str, *, cookie_header: str | None = None) -> ModelSpec:
        logical = self._runtime.logical_model(model_id)
        if logical is not None:
            if context.user.role is not PortalRole.ADMIN and model_id not in self._store.governed_assigned_models(context.user.user_id):
                raise ValueError("model is unavailable")
            if not logical.enabled or logical.archived_at is not None or not self._runtime.has_healthy_route(logical):
                raise ValueError("model is unavailable")
            try:
                return _logical_model_spec(logical)
            except ValueError:
                raise ValueError("model is unavailable") from None
        return await self._base.resolve_model(context, model_id, cookie_header=cookie_header)

    def model_binding(self, model_id: str):
        resolver = getattr(self._base, "model_binding", None)
        return resolver(model_id) if callable(resolver) else None


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

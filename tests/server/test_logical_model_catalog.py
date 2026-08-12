from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_creation_canvas.adapters.portal.catalog import CatalogResult
from ai_creation_canvas.catalog import LogicalModelCatalog, ManagedRoutingRuntime
from ai_creation_canvas.credential_pools import CredentialKey, CredentialPool
from ai_creation_canvas.domain.models import ModelInputPort, PortalRole, PortalUser, RequestContext
from ai_creation_canvas.model_registry import OperationContract
from ai_creation_canvas.model_registry import ProviderDefinition
from ai_creation_canvas.model_routing import LogicalModelDefinition, ModelRouteDefinition
from ai_creation_canvas.routing import RouteSelector
from ai_creation_canvas.storage.sqlite import CanvasStore


def contract(operation: str = "image.edit") -> OperationContract:
    return OperationContract(
        operation,
        (ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 1, 10)),
        "image",
        {"type": "object", "properties": {"size": {"type": "string", "enum": ["auto", "1024x1024"], "default": "auto"}, "output_count": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1}}, "required": ["size", "output_count"], "additionalProperties": False},
        {"size": "size", "output_count": "n"},
    )


def model(*, enabled: bool = True, archived_at: str | None = None, contracts: tuple[OperationContract, ...] | None = None) -> LogicalModelDefinition:
    return LogicalModelDefinition("nano-banana", "Nano Banana", "Logical image model", "image", contracts or (contract(),), enabled, archived_at, 1)


def route(name: str, provider: str, pool: str, priority: int) -> ModelRouteDefinition:
    return ModelRouteDefinition(name, "nano-banana", provider, "gemini-image", "chiyun_openai_images", pool, "nano-banana", (contract(),), priority, 2, revision=1)


def pool(name: str, provider: str, group: str) -> CredentialPool:
    return CredentialPool(name, provider, group, ("nano-banana",), (CredentialKey(f"{name}-key", f"test-secret-{name}", 1),), name[0] * 64)


class EmptyCatalog:
    async def list_models(self, context, *, cookie_header=None):
        return CatalogResult((), ())

    async def resolve_model(self, context, model_id, *, cookie_header=None):
        raise ValueError("model is unavailable")


class NeverCoordinator:
    def acquire(self, *args, **kwargs):
        raise AssertionError("legacy acquire must not be used")

    def acquire_credential(self, *args, **kwargs):
        raise AssertionError("catalog must not acquire credentials")


class NeverFactory:
    def build(self, *args, **kwargs):
        raise AssertionError("catalog must not build adapters")


def runtime(store: CanvasStore) -> ManagedRoutingRuntime:
    pools = {"official": pool("official", "google", "official"), "gemini": pool("gemini", "t8star", "gemini"), "backup": pool("backup", "backup", "official")}
    return ManagedRoutingRuntime(store, lambda: pools, RouteSelector(), NeverCoordinator(), NeverFactory())


def test_public_catalog_projects_one_logical_model_without_routing_fields(tmp_path: Path) -> None:
    store = CanvasStore(tmp_path / "data")
    for provider_id in ("google", "t8star", "backup"):
        store.create_provider_definition(ProviderDefinition(provider_id, provider_id, "chiyun_openai_images", f"https://{provider_id}.example", provider_id), actor_user_id="bootstrap")
    store.create_logical_model(model())
    for item in (route("official-route", "google", "official", 1), route("gemini-route", "t8star", "gemini", 2), route("backup-route", "backup", "backup", 3)):
        store.create_model_route(item)
    catalog = LogicalModelCatalog(EmptyCatalog(), store, runtime(store))
    context = RequestContext(PortalUser("admin", "Admin", PortalRole.ADMIN), "request", "trace")

    result = asyncio.run(catalog.list_models(context))

    assert len(result.models) == 1
    public = json.dumps(result.models[0], default=lambda value: value.__dict__ if hasattr(value, "__dict__") else str(value))
    assert result.models[0].model_id == "nano-banana"
    assert result.models[0].service_id == "nano-banana"
    for forbidden in ("provider", "adapter", "route", "group", "pool", "key", "fingerprint", "origin", "secret"):
        assert forbidden not in public.lower()


def test_incompatible_multi_operation_projection_fails_closed(tmp_path: Path) -> None:
    store = CanvasStore(tmp_path / "data")
    generate = OperationContract(
        "image.generate", (ModelInputPort("prompt", "text", 1, 1),), "image",
        {"type": "object", "properties": {}, "additionalProperties": False}, {},
    )
    store.create_logical_model(model(contracts=(contract(), generate)))
    catalog = LogicalModelCatalog(EmptyCatalog(), store, runtime(store))
    context = RequestContext(PortalUser("admin", "Admin", PortalRole.ADMIN), "request", "trace")

    result = asyncio.run(catalog.list_models(context))

    assert result.models == ()
    with pytest.raises(ValueError, match="unavailable"):
        asyncio.run(catalog.resolve_model(context, "nano-banana"))

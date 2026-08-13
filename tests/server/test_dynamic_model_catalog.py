from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from ai_creation_canvas.adapters.factory import AdapterFactory, MappingCredentialResolver
from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.catalog import AssignedModelCatalog, GovernedModelCatalog
from ai_creation_canvas.domain.models import PortalRole, PortalUser, RequestContext
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.storage.sqlite import CanvasStore
from tests.server.test_model_registry import _model, _provider


def _context(user_id: str, role: PortalRole) -> RequestContext:
    return RequestContext(PortalUser(user_id, user_id, role), f"request-{user_id}", f"trace-{user_id}")


def _catalog(tmp_path: Path):
    store = CanvasStore(tmp_path / "data")
    store.create_user(user_id="user-1", username_normalized="user-1", display_name="User", password_hash="hash", role="user", must_change_password=False)
    store.create_provider_definition(_provider(), actor_user_id="admin")
    store.create_model_definition(_model(), actor_user_id="admin")
    registry = AdapterRegistry()
    factory = AdapterFactory(data_dir=store.data_dir, credential_resolver=MappingCredentialResolver({"chiyun-primary": "test-only-secret"}), asset_loader=lambda _: (b"\x89PNG\r\n\x1a\n", "image/png"), transport=httpx.MockTransport(lambda _: httpx.Response(500)), trusted_provider_origins={("chiyun", "chiyun_openai_images"): "https://chiyun.example"})
    governed = GovernedModelCatalog(ModelCatalog(registry), store, registry, factory)
    return store, registry, AssignedModelCatalog(governed, store)


def test_governed_catalog_filters_access_and_registers_only_the_declared_image_operation(tmp_path: Path) -> None:
    store, registry, catalog = _catalog(tmp_path)

    async def scenario() -> None:
        admin = await catalog.list_models(_context("admin", PortalRole.ADMIN))
        hidden = await catalog.list_models(_context("user-1", PortalRole.USER))
        store.grant_model_access("user-1", "chiyun-gpt-image-2", actor_user_id="admin")
        visible = await catalog.list_models(_context("user-1", PortalRole.USER))
        assert [model.model_id for model in admin.models] == ["chiyun-gpt-image-2"]
        assert hidden.models == ()
        assert [model.model_id for model in visible.models] == ["chiyun-gpt-image-2"]
        assert [operation.value for operation in visible.models[0].operations] == ["image.edit"]
        assert visible.models[0].parameter_mappings == {}
        assert registry.generation("chiyun").service_id == "chiyun"
        binding = catalog.model_binding("chiyun-gpt-image-2")
        assert binding is not None and binding.model.revision == 1 and binding.provider.adapter_type == "chiyun_openai_images"
        store.revoke_model_access("user-1", "chiyun-gpt-image-2", actor_user_id="admin")
        try:
            await catalog.resolve_model(_context("user-1", PortalRole.USER), "chiyun-gpt-image-2")
        except ValueError:
            pass
        else:
            raise AssertionError("revoked model remained resolvable")

    asyncio.run(scenario())


def test_disabled_provider_removes_model_from_catalog(tmp_path: Path) -> None:
    store, registry, catalog = _catalog(tmp_path)
    del registry
    store.update_provider_definition(_provider(enabled=False), expected_revision=1, actor_user_id="admin")
    result = asyncio.run(catalog.list_models(_context("admin", PortalRole.ADMIN)))
    assert result.models == ()

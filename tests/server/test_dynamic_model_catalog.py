from __future__ import annotations

import asyncio
import base64
from pathlib import Path
import time

import httpx
from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.factory import AdapterFactory, MappingCredentialResolver
from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.catalog import AssignedModelCatalog, GovernedModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import JobRequest, PortalRole, PortalUser, RequestContext
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


def test_startup_preloads_governed_direct_adapter_and_recovers_without_catalog_request(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    store = CanvasStore(data_dir)
    store.create_provider_definition(_provider(), actor_user_id="admin")
    store.create_model_definition(_model(), actor_user_id="admin")
    encoded = base64.b64encode(b"\x89PNG\r\n\x1a\nrecovery").decode("ascii")
    submission_factory = AdapterFactory(
        data_dir=data_dir,
        credential_resolver=MappingCredentialResolver({"chiyun-primary": "test-only-secret"}),
        asset_loader=lambda _: (b"\x89PNG\r\n\x1a\ninput", "image/png"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"data": [{"b64_json": encoded}]})
        ),
        trusted_provider_origins={("chiyun", "chiyun_openai_images"): "https://chiyun.example"},
    )
    adapter = submission_factory.build(_provider(), (_model(),))
    upstream = asyncio.run(adapter.submit(
        _context("user-1", PortalRole.USER),
        JobRequest(
            "image.edit", "chiyun-gpt-image-2", "recover", "restart-job",
            {"size": "auto", "output_count": 1},
            inputs={"reference_images": ("input",)},
        ),
    ))
    reservation = store.reserve_job(
        user_id="user-1", job_id="restart-job", service_id="chiyun",
        operation="image.edit", idempotency_key="restart-job", request_hash="r" * 64,
        model_id="chiyun-gpt-image-2", model_revision=1,
        provider_id="chiyun", adapter_type="chiyun_openai_images",
    )
    store.mark_submitted(
        "restart-job", upstream.upstream_job_id, "queued",
        str(reservation.job["submission_token"]),
    )
    with store._connection(immediate=True) as db:
        db.execute(
            "UPDATE canvas_jobs SET completion_mode=NULL WHERE id='restart-job'"
        )

    recovery_calls = 0

    def no_provider_call(_: httpx.Request) -> httpx.Response:
        nonlocal recovery_calls
        recovery_calls += 1
        raise AssertionError("restart recovery must use the local pending index")

    registry = AdapterRegistry()
    recovery_factory = AdapterFactory(
        data_dir=data_dir,
        credential_resolver=MappingCredentialResolver({"chiyun-primary": "test-only-secret"}),
        asset_loader=lambda _: (b"\x89PNG\r\n\x1a\ninput", "image/png"),
        transport=httpx.MockTransport(no_provider_call),
        trusted_provider_origins={("chiyun", "chiyun_openai_images"): "https://chiyun.example"},
    )
    app = create_app(
        Settings("test", 8992, data_dir, "test-secret"),
        static_dir=tmp_path / "dist",
        registry=registry,
        canvas_store=store,
        adapter_factory=recovery_factory,
    )

    with TestClient(app, raise_server_exceptions=False):
        deadline = time.monotonic() + 0.5
        item, forbidden = store.job_for_owner("restart-job", "user-1")
        while item is not None and item["status"] != "succeeded" and time.monotonic() < deadline:
            time.sleep(0.01)
            item, forbidden = store.job_for_owner("restart-job", "user-1")

    assert item is not None and forbidden is False
    assert item["status"] == "succeeded"
    assert item["completion_mode"] == "background"
    assert registry.generation("chiyun").service_id == "chiyun"
    assert recovery_calls == 0


def test_startup_does_not_reconcile_spoofed_request_scoped_capability(tmp_path: Path) -> None:
    class SpoofedAdapter:
        service_id = "spoofed"
        requires_request_scoped_polling = True

        async def list_models(self, context):
            return ()

        async def submit(self, context, request):
            raise AssertionError("startup must not submit")

        async def poll(self, context, upstream_job_id):
            raise AssertionError("startup must not poll")

    store = CanvasStore(tmp_path / "spoofed-data")
    reservation = store.reserve_job(
        user_id="user-1",
        job_id="spoofed-job",
        service_id="spoofed",
        operation="image.generate",
        idempotency_key="spoofed-job",
        request_hash="s" * 64,
    )
    store.mark_submitted(
        "spoofed-job", "spoofed-upstream", "queued", str(reservation.job["submission_token"])
    )
    with store._connection(immediate=True) as db:
        db.execute("UPDATE canvas_jobs SET completion_mode=NULL WHERE id='spoofed-job'")
    registry = AdapterRegistry()
    registry.register_generation(SpoofedAdapter())
    app = create_app(
        Settings("test", 8992, store.data_dir, "test-secret"),
        registry=registry,
        model_catalog=ModelCatalog(registry),
        canvas_store=store,
    )

    with TestClient(app, raise_server_exceptions=False):
        pass

    stored, forbidden = store.job_for_owner("spoofed-job", "user-1")
    assert stored is not None and forbidden is False
    assert stored["completion_mode"] is None
    assert store.claim_pollable_job() is None
    assert store.claim_request_scoped_job(
        "spoofed-job", user_id="user-1", lease_seconds=30
    ) is None

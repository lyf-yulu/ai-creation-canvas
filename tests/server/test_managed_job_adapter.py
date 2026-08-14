from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from ai_creation_canvas.catalog import ManagedRoutingRuntime
from ai_creation_canvas.coordination import CredentialLease, CoordinationUnavailable
from ai_creation_canvas.credential_pools import CredentialKey, CredentialPool
from ai_creation_canvas.domain.models import PortalUser, RequestContext
from ai_creation_canvas.managed_jobs import managed_job_adapter, validated_job_route
from ai_creation_canvas.model_registry import ProviderDefinition
from ai_creation_canvas.model_routing import LogicalModelDefinition, ModelRouteDefinition
from ai_creation_canvas.routing import RouteSelector
from ai_creation_canvas.storage.sqlite import CanvasStore
from ai_creation_canvas.trusted_routing import trusted_route_presets


POOL_DIGEST = "a" * 64


def _route() -> ModelRouteDefinition:
    preset = trusted_route_presets()[("banana", "chiyun")]
    return ModelRouteDefinition(
        "banana-chiyun",
        "banana",
        preset.provider_id,
        preset.provider_model_name,
        preset.adapter_type,
        "banana-pool",
        preset.family,
        preset.operation_contracts,
        10,
        2,
    )


def _snapshot(route: ModelRouteDefinition, shape: str = "v2") -> str:
    body: dict[str, object] = {
        "route_id": route.route_id,
        "model_id": route.model_id,
        "provider_id": route.provider_id,
        "provider_model_name": route.provider_model_name,
        "adapter_type": route.adapter_type,
        "credential_pool_ref": route.credential_pool_ref,
        "family": route.family,
        "operation_contracts": [contract.to_dict() for contract in route.operation_contracts],
        "priority": route.priority,
        "max_concurrency": route.max_concurrency,
        "enabled": route.enabled,
        "archived_at": route.archived_at,
        "revision": route.revision,
    }
    if shape in {"digest", "v2"}:
        body["pool_revision_digest"] = POOL_DIGEST
    if shape == "v2":
        body["schema_version"] = 2
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _job(route: ModelRouteDefinition, shape: str = "v2") -> dict[str, object]:
    return {
        "id": "managed-job",
        "user_id": "user-a",
        "service_id": route.model_id,
        "operation": route.operation_contracts[0].operation.value,
        "logical_model_id": route.model_id,
        "route_id": route.route_id,
        "route_revision": route.revision,
        "pool_revision_digest": POOL_DIGEST,
        "key_fingerprint": "b" * 64,
        "route_snapshot_json": _snapshot(route, shape),
    }


@pytest.mark.parametrize("shape", ("legacy-v1", "digest", "v2"))
def test_validated_job_route_accepts_each_persisted_snapshot_shape(shape: str) -> None:
    route = _route()

    resolved = validated_job_route(_job(route, shape))

    assert resolved == route


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("service_id", "other-service"),
        ("logical_model_id", "other-model"),
        ("route_id", "other-route"),
        ("operation", "image.generate"),
        ("route_revision", 2),
        ("pool_revision_digest", "c" * 64),
    ),
)
def test_validated_job_route_rejects_job_columns_inconsistent_with_snapshot(field: str, value: object) -> None:
    item = _job(_route())
    item[field] = value

    with pytest.raises(ValueError, match="inconsistent"):
        validated_job_route(item)


def test_validated_job_route_rejects_future_snapshot_shape() -> None:
    item = _job(_route())
    snapshot = json.loads(str(item["route_snapshot_json"]))
    snapshot["schema_version"] = 3
    item["route_snapshot_json"] = json.dumps(snapshot)

    with pytest.raises(ValueError, match="invalid"):
        validated_job_route(item)


class RecordingCoordinator:
    def __init__(self) -> None:
        self.candidates = []

    def fingerprint_secret(self, secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()

    @asynccontextmanager
    async def acquire_credential(self, job_id, user_id, candidate):
        del job_id, user_id
        self.candidates.append(candidate)
        key = candidate.pool.keys[0]
        yield CredentialLease(
            candidate.route.route_id,
            candidate.pool.pool_id,
            key.key_id,
            key.secret,
            self.fingerprint_secret(key.secret),
            "owner",
        )


class RecordingFactory:
    def build(self, route, lease):
        return (route.route_id, lease.key_id)


def _runtime(tmp_path: Path, *, include_original_key: bool = True):
    store = CanvasStore(tmp_path / "data")
    route = _route()
    model = LogicalModelDefinition(
        route.model_id,
        "Banana",
        "Managed image model.",
        "image",
        route.operation_contracts,
    )
    provider = ProviderDefinition(
        route.provider_id,
        "Chiyun Banana",
        route.adapter_type,
        "https://chiyun.work",
        "unused",
    )
    store.create_provider_definition(provider, actor_user_id="bootstrap")
    store.create_logical_model(model)
    store.create_model_route(route)
    original = CredentialKey("original", "test-original-value", 1)
    alternate = CredentialKey("alternate", "test-alternate-value", 1)
    keys = (original, alternate) if include_original_key else (alternate,)
    pool = CredentialPool(
        route.credential_pool_ref,
        route.provider_id,
        "test",
        (route.family,),
        keys,
        POOL_DIGEST,
    )
    coordinator = RecordingCoordinator()
    runtime = ManagedRoutingRuntime(
        store,
        lambda: {pool.pool_id: pool},
        RouteSelector(trusted_routes_only=True),
        coordinator,
        RecordingFactory(),
    )
    expected_fingerprint = coordinator.fingerprint_secret(original.secret)
    reserved = store.reserve_job(
        user_id="user-a",
        job_id="managed-job",
        service_id=route.model_id,
        operation=route.operation_contracts[0].operation.value,
        idempotency_key="managed-key",
        request_hash="f" * 64,
        model_id=route.model_id,
        logical_model_id=route.model_id,
        logical_model_revision=model.revision,
    )
    token = str(reserved.job["submission_token"])
    store.record_routing_snapshot(
        "managed-job",
        token,
        logical_model_id=route.model_id,
        logical_model_revision=model.revision,
        route_id=route.route_id,
        route_revision=route.revision,
        pool_revision_digest=POOL_DIGEST,
        key_fingerprint=expected_fingerprint,
        route_snapshot_json=_snapshot(route),
    )
    item = store.mark_submitted("managed-job", "upstream-job", "queued", token)
    context = RequestContext(PortalUser("user-a", "Alice", "user"), "request-a", "trace-a")
    return runtime, context, item, coordinator


@pytest.mark.anyio
async def test_managed_job_adapter_selects_only_the_exact_stored_fingerprint(tmp_path: Path) -> None:
    runtime, context, item, coordinator = _runtime(tmp_path)

    async with managed_job_adapter(runtime, context, item) as adapter:
        assert adapter == ("banana-chiyun", "original")

    assert len(coordinator.candidates) == 1
    assert tuple(key.key_id for key in coordinator.candidates[0].pool.keys) == ("original",)


@pytest.mark.anyio
async def test_managed_job_adapter_never_substitutes_another_compatible_key(tmp_path: Path) -> None:
    runtime, context, item, coordinator = _runtime(tmp_path, include_original_key=False)

    with pytest.raises(CoordinationUnavailable, match="credential is unavailable"):
        async with managed_job_adapter(runtime, context, item):
            raise AssertionError("unreachable")

    assert coordinator.candidates == []


@pytest.mark.parametrize(
    "change",
    (
        "provider-disabled",
        "provider-untrusted",
        "model-disabled",
        "model-archived",
        "model-purged",
        "route-disabled",
        "route-archived",
        "route-purged",
        "route-untrusted",
    ),
)
@pytest.mark.anyio
async def test_managed_job_adapter_rechecks_current_governed_state_before_credentials(tmp_path: Path, change: str) -> None:
    runtime, context, item, coordinator = _runtime(tmp_path)
    store = runtime.store
    provider = store.provider_definition("chiyun-banana")
    model = store.logical_model("banana")
    route = store.model_route("banana-chiyun")
    assert provider is not None and isinstance(model, LogicalModelDefinition) and isinstance(route, ModelRouteDefinition)

    if change == "provider-disabled":
        store.update_provider_definition(replace(provider, enabled=False), expected_revision=1, actor_user_id="admin")
    elif change == "provider-untrusted":
        store.update_provider_definition(replace(provider, base_url="https://other.example"), expected_revision=1, actor_user_id="admin")
    elif change == "model-disabled":
        store.set_logical_model_enabled("banana", enabled=False, expected_revision=1)
    elif change == "model-archived":
        store.archive_logical_model("banana", expected_revision=1)
    elif change == "model-purged":
        store.purge_logical_model_runtime("banana", expected_revision=1)
    elif change == "route-disabled":
        store.set_model_route_enabled("banana-chiyun", enabled=False, expected_revision=1)
    elif change == "route-archived":
        store.archive_model_route("banana-chiyun", expected_revision=1)
    elif change == "route-purged":
        store.purge_model_route_runtime("banana-chiyun", expected_revision=1)
    else:
        store.update_model_route(replace(route, provider_model_name="historical-model"), expected_revision=1)

    with pytest.raises(ValueError):
        async with managed_job_adapter(runtime, context, item):
            raise AssertionError("unreachable")

    assert coordinator.candidates == []

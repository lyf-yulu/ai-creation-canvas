"""Shared fail-closed resolution for persisted managed generation jobs."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace

from ai_creation_canvas.catalog import ManagedRoutingRuntime
from ai_creation_canvas.coordination import CoordinationUnavailable
from ai_creation_canvas.credential_pools import CredentialPool
from ai_creation_canvas.domain.models import RequestContext
from ai_creation_canvas.model_registry import OperationContract
from ai_creation_canvas.model_routing import LogicalModelDefinition, ModelRouteDefinition
from ai_creation_canvas.routing import RouteCandidate
from ai_creation_canvas.trusted_routing import provider_has_trusted_origin, validate_trusted_route


_MAX_SNAPSHOT_BYTES = 64 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SNAPSHOT_CORE = {
    "route_id",
    "model_id",
    "provider_id",
    "provider_model_name",
    "adapter_type",
    "credential_pool_ref",
    "family",
    "operation_contracts",
    "priority",
    "max_concurrency",
    "enabled",
    "archived_at",
    "revision",
}
_ACCEPTED_SNAPSHOT_SHAPES = {
    frozenset(_SNAPSHOT_CORE),
    frozenset(_SNAPSHOT_CORE | {"pool_revision_digest"}),
    frozenset(_SNAPSHOT_CORE | {"pool_revision_digest", "schema_version"}),
}


def _route_from_snapshot(value: object) -> tuple[ModelRouteDefinition, Mapping[str, object]]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
        raise ValueError("managed route snapshot is invalid")
    try:
        body = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("managed route snapshot is invalid") from None
    if (
        not isinstance(body, dict)
        or frozenset(body) not in _ACCEPTED_SNAPSHOT_SHAPES
        or ("schema_version" in body and body["schema_version"] != 2)
        or not isinstance(body["operation_contracts"], list)
    ):
        raise ValueError("managed route snapshot is invalid")
    try:
        contracts = tuple(
            OperationContract.from_dict(item)
            for item in body["operation_contracts"]
            if isinstance(item, dict)
        )
        if len(contracts) != len(body["operation_contracts"]):
            raise ValueError
        route = ModelRouteDefinition(
            route_id=body["route_id"],
            model_id=body["model_id"],
            provider_id=body["provider_id"],
            provider_model_name=body["provider_model_name"],
            adapter_type=body["adapter_type"],
            credential_pool_ref=body["credential_pool_ref"],
            family=body["family"],
            operation_contracts=contracts,
            priority=body["priority"],
            max_concurrency=body["max_concurrency"],
            enabled=body["enabled"],
            archived_at=body["archived_at"],
            revision=body["revision"],
        )
    except (TypeError, ValueError, KeyError):
        raise ValueError("managed route snapshot is invalid") from None
    return route, body


def validated_job_route(item: Mapping[str, object]) -> ModelRouteDefinition:
    """Parse an accepted immutable snapshot and bind it to its stored job columns."""
    route, body = _route_from_snapshot(item.get("route_snapshot_json"))
    digest = item.get("pool_revision_digest")
    if (
        item.get("logical_model_id") != route.model_id
        or item.get("service_id") != route.model_id
        or item.get("route_id") != route.route_id
        or item.get("route_revision") != route.revision
        or item.get("operation") not in {contract.operation.value for contract in route.operation_contracts}
        or not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
        or ("pool_revision_digest" in body and digest != body.get("pool_revision_digest"))
    ):
        raise ValueError("managed job snapshot is inconsistent")
    return route


def _require_current_governed_route(
    runtime: ManagedRoutingRuntime,
    snapshot: ModelRouteDefinition,
) -> None:
    store = runtime.store
    provider = store.provider_definition(snapshot.provider_id)
    model = store.logical_model(snapshot.model_id)
    current_route = store.model_route(snapshot.route_id)
    if (
        not provider_has_trusted_origin(provider)
        or not isinstance(model, LogicalModelDefinition)
        or not model.enabled
        or model.archived_at is not None
        or not isinstance(current_route, ModelRouteDefinition)
        or not current_route.enabled
        or current_route.archived_at is not None
    ):
        raise ValueError("managed route is unavailable")
    try:
        validate_trusted_route(snapshot, model)
        validate_trusted_route(current_route, model)
    except ValueError:
        raise ValueError("managed route is unavailable") from None


@asynccontextmanager
async def managed_job_adapter(
    runtime: ManagedRoutingRuntime,
    context: RequestContext,
    item: Mapping[str, object],
) -> AsyncIterator[object]:
    """Build a short-lived adapter using only the job's original credential."""
    if not isinstance(runtime, ManagedRoutingRuntime) or not isinstance(context, RequestContext):
        raise ValueError("managed adapter context is invalid")
    route = validated_job_route(item)
    _require_current_governed_route(runtime, route)
    expected = item.get("key_fingerprint")
    if not isinstance(expected, str) or not expected:
        raise ValueError("managed credential snapshot is unavailable")
    pool = runtime.pools().get(route.credential_pool_ref)
    if not isinstance(pool, CredentialPool):
        raise CoordinationUnavailable("managed credential pool is unavailable")
    matches = tuple(
        key
        for key in pool.keys
        if runtime.coordinator.fingerprint_secret(key.secret) == expected
    )
    if len(matches) != 1:
        raise CoordinationUnavailable("managed credential is unavailable")
    candidate = RouteCandidate(route, replace(pool, keys=matches))
    async with runtime.coordinator.acquire_credential(
        str(item["id"]), context.user.user_id, candidate
    ) as lease:
        if lease.key_fingerprint != expected:
            raise CoordinationUnavailable("managed credential changed")
        yield runtime.adapter_factory.build(route, lease)

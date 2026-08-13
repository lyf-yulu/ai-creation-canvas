"""Pure selection of model routes that can honor one validated request."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ai_creation_canvas.credential_pools import CredentialPool
from ai_creation_canvas.domain.models import ModelOperation
from ai_creation_canvas.model_routing import (
    LogicalModelDefinition,
    ModelRouteDefinition,
    validate_route_model,
    validate_route_pool,
)
from ai_creation_canvas.parameter_schema import validate_parameter_values


_MEDIA_TYPES = frozenset({"text", "image", "video", "audio"})
_MAX_INPUT_PORTS = 16
_MAX_INPUT_ITEMS = 64
_MAX_PARAMETERS = 64


@dataclass(frozen=True, slots=True, repr=False)
class RouteCandidate:
    route: ModelRouteDefinition
    pool: CredentialPool

    def __post_init__(self) -> None:
        validate_route_pool(self.route, self.pool)
        if not self.pool.keys:
            raise ValueError("credential pool is empty")

    def __repr__(self) -> str:
        return (
            "RouteCandidate("
            f"route_id={self.route.route_id!r}, pool_id={self.pool.pool_id!r}, "
            f"provider_id={self.route.provider_id!r})"
        )


class RouteSelector:
    def __init__(self, *, unhealthy_route_ids: frozenset[str] = frozenset(), trusted_routes_only: bool = False) -> None:
        if not isinstance(unhealthy_route_ids, frozenset) or any(not isinstance(item, str) or not item for item in unhealthy_route_ids):
            raise ValueError("unhealthy route snapshot is invalid")
        if type(trusted_routes_only) is not bool:
            raise ValueError("trusted route selection flag is invalid")
        self._unhealthy_route_ids = unhealthy_route_ids
        self._trusted_routes_only = trusted_routes_only

    def accepts_trusted_route(self, route: ModelRouteDefinition, model: LogicalModelDefinition) -> bool:
        if not self._trusted_routes_only:
            return True
        from ai_creation_canvas.trusted_routing import validate_trusted_route

        try:
            validate_trusted_route(route, model)
        except ValueError:
            return False
        return True

    def candidates(
        self,
        model: LogicalModelDefinition,
        operation: ModelOperation | str,
        params: Mapping[str, object],
        inputs: Mapping[str, tuple[str, ...]],
        routes: Sequence[ModelRouteDefinition],
        pools: Mapping[str, CredentialPool],
    ) -> tuple[RouteCandidate, ...]:
        try:
            selected_operation = ModelOperation(operation)
        except (TypeError, ValueError):
            return ()
        if not isinstance(model, LogicalModelDefinition) or not model.enabled or model.archived_at is not None:
            return ()
        if not isinstance(params, Mapping) or len(params) > _MAX_PARAMETERS:
            return ()
        facts = _validated_input_facts(inputs)
        if facts is None:
            return ()
        model_contract = next((item for item in model.operation_contracts if item.operation is selected_operation), None)
        if model_contract is None or not _request_matches_contract(model_contract, params, facts):
            return ()

        candidates: list[RouteCandidate] = []
        for route in routes:
            if (
                not isinstance(route, ModelRouteDefinition)
                or route.model_id != model.model_id
                or not route.enabled
                or route.archived_at is not None
                or route.route_id in self._unhealthy_route_ids
            ):
                continue
            if not self.accepts_trusted_route(route, model):
                continue
            pool = pools.get(route.credential_pool_ref)
            if not isinstance(pool, CredentialPool) or not pool.keys:
                continue
            try:
                validate_route_model(route, model)
                validate_route_pool(route, pool)
            except ValueError:
                continue
            route_contract = next((item for item in route.operation_contracts if item.operation is selected_operation), None)
            if route_contract is None or not _request_matches_contract(route_contract, params, facts):
                continue
            candidates.append(RouteCandidate(route=route, pool=pool))
        return tuple(sorted(candidates, key=lambda item: (item.route.priority, item.route.route_id)))


def _validated_input_facts(inputs: object) -> dict[str, tuple[str, ...]] | None:
    if not isinstance(inputs, Mapping) or len(inputs) > _MAX_INPUT_PORTS:
        return None
    result: dict[str, tuple[str, ...]] = {}
    total = 0
    for raw_name, raw_items in inputs.items():
        if not isinstance(raw_name, str) or not raw_name or not isinstance(raw_items, tuple):
            return None
        if any(item not in _MEDIA_TYPES for item in raw_items):
            return None
        total += len(raw_items)
        if total > _MAX_INPUT_ITEMS:
            return None
        result[raw_name] = raw_items
    return result


def _request_matches_contract(contract, params: Mapping[str, object], inputs: Mapping[str, tuple[str, ...]]) -> bool:
    try:
        validate_parameter_values(contract.parameter_schema, params)
    except (TypeError, ValueError):
        return False
    required = contract.parameter_schema.get("required", ())
    if not isinstance(required, (list, tuple)) or any(not isinstance(item, str) for item in required):
        return False
    if not set(required) <= set(params):
        return False
    ports = {port.port_id: port for port in contract.input_ports}
    if set(inputs) - set(ports):
        return False
    for port in contract.input_ports:
        items = inputs.get(port.port_id, ())
        if not port.min_items <= len(items) <= port.max_items:
            return False
        if any(item != port.media_type for item in items):
            return False
    return True

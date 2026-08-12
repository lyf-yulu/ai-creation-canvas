from __future__ import annotations

from dataclasses import replace

from ai_creation_canvas.credential_pools import CredentialKey, CredentialPool
from ai_creation_canvas.domain.models import ModelInputPort, ModelOperation
from ai_creation_canvas.model_registry import OperationContract
from ai_creation_canvas.model_routing import LogicalModelDefinition, ModelRouteDefinition
from ai_creation_canvas.routing import RouteSelector


def _contract(
    *,
    sizes: tuple[str, ...] = ("1024x1024", "1536x1024"),
    reference_min: int = 1,
    reference_max: int = 15,
    include_seed: bool = True,
) -> OperationContract:
    properties: dict[str, object] = {
        "size": {"type": "string", "enum": list(sizes)},
        "output_count": {"type": "integer", "minimum": 1, "maximum": 4},
    }
    if include_seed:
        properties["seed"] = {"type": "integer", "minimum": 0, "maximum": 2_147_483_647}
    return OperationContract(
        ModelOperation.IMAGE_EDIT,
        (
            ModelInputPort("prompt", "text", 1, 1),
            ModelInputPort("reference_images", "image", reference_min, reference_max),
        ),
        "image",
        {"type": "object", "properties": properties, "additionalProperties": False},
        {name: name for name in properties},
    )


def _model(**changes: object) -> LogicalModelDefinition:
    values: dict[str, object] = {
        "model_id": "nano-banana",
        "display_name": "Nano Banana",
        "introduction": "Multi-reference image editing.",
        "modality": "image",
        "operation_contracts": (_contract(),),
        "enabled": True,
        "archived_at": None,
        "revision": 1,
    }
    values.update(changes)
    return LogicalModelDefinition(**values)  # type: ignore[arg-type]


def _route(route_id: str, provider: str, pool_id: str, *, priority: int, contract: OperationContract | None = None, family: str = "nano-banana", **changes: object) -> ModelRouteDefinition:
    values: dict[str, object] = {
        "route_id": route_id,
        "model_id": "nano-banana",
        "provider_id": provider,
        "provider_model_name": "gemini-2.5-flash-image",
        "adapter_type": "chiyun_openai_images",
        "credential_pool_ref": pool_id,
        "family": family,
        "operation_contracts": (contract or _contract(),),
        "priority": priority,
        "max_concurrency": 8,
        "enabled": True,
        "archived_at": None,
        "revision": 1,
    }
    values.update(changes)
    return ModelRouteDefinition(**values)  # type: ignore[arg-type]


def _pool(pool_id: str, provider: str, group: str, families: tuple[str, ...]) -> CredentialPool:
    return CredentialPool(
        pool_id=pool_id,
        provider_id=provider,
        group=group,
        allowed_families=families,
        keys=(CredentialKey(f"{pool_id}-01", f"secret-{pool_id}", 4),),
        revision_digest="a" * 64,
    )


def _facts(reference_count: int = 2) -> dict[str, tuple[str, ...]]:
    return {"prompt": ("text",), "reference_images": ("image",) * reference_count}


def test_selector_accepts_official_and_gemini_but_never_cc_pool() -> None:
    official = _route("official-route", "google", "official", priority=20)
    gemini = _route("gemini-route", "t8star", "t8-gemini", priority=10)
    cc = _route("cc-route", "t8star", "t8-cc", priority=1)
    pools = {
        "official": _pool("official", "google", "official", ("nano-banana",)),
        "t8-gemini": _pool("t8-gemini", "t8star", "gemini", ("gemini", "nano-banana")),
        "t8-cc": _pool("t8-cc", "t8star", "cc", ("claude",)),
    }

    candidates = RouteSelector().candidates(
        _model(), ModelOperation.IMAGE_EDIT,
        {"size": "1024x1024", "output_count": 1}, _facts(),
        (official, gemini, cc), pools,
    )

    assert tuple(item.route.route_id for item in candidates) == ("gemini-route", "official-route")
    assert tuple(item.pool.pool_id for item in candidates) == ("t8-gemini", "official")


def test_route_specific_parameter_support_removes_only_that_route() -> None:
    full = _route("full-route", "google", "official", priority=20)
    limited = _route(
        "limited-route", "t8star", "t8-gemini", priority=10,
        contract=_contract(include_seed=False),
    )
    pools = {
        "official": _pool("official", "google", "official", ("nano-banana",)),
        "t8-gemini": _pool("t8-gemini", "t8star", "gemini", ("nano-banana",)),
    }

    candidates = RouteSelector().candidates(
        _model(), "image.edit", {"size": "1536x1024", "output_count": 1, "seed": 7},
        _facts(), (limited, full), pools,
    )

    assert tuple(item.route.route_id for item in candidates) == ("full-route",)


def test_invalid_public_parameter_request_never_weakens_the_model_contract() -> None:
    route = _route("route-a", "google", "official", priority=1, contract=_contract(include_seed=False))
    pools = {"official": _pool("official", "google", "official", ("nano-banana",))}

    candidates = RouteSelector().candidates(
        _model(), "image.edit", {"size": "not-a-public-size", "output_count": 1},
        _facts(), (route,), pools,
    )

    assert candidates == ()


def test_selector_enforces_named_input_presence_media_and_counts() -> None:
    route = _route("route-a", "google", "official", priority=1, contract=_contract(reference_max=2))
    pools = {"official": _pool("official", "google", "official", ("nano-banana",))}
    selector = RouteSelector()
    args = (_model(), "image.edit", {"size": "1024x1024", "output_count": 1})

    assert selector.candidates(*args, {"reference_images": ("image",)}, (route,), pools) == ()
    assert selector.candidates(*args, {"prompt": ("image",), "reference_images": ("image",)}, (route,), pools) == ()
    assert selector.candidates(*args, _facts(3), (route,), pools) == ()
    assert selector.candidates(*args, {**_facts(), "reference_video": ("video",)}, (route,), pools) == ()
    assert tuple(item.route.route_id for item in selector.candidates(*args, _facts(2), (route,), pools)) == ("route-a",)


def test_optional_public_input_may_be_absent_but_an_omitted_route_port_cannot_receive_it() -> None:
    public_contract = _contract(reference_min=0)
    route_contract = OperationContract(
        ModelOperation.IMAGE_EDIT,
        (ModelInputPort("prompt", "text", 1, 1),),
        "image",
        public_contract.to_dict()["parameter_schema"],  # type: ignore[arg-type]
        dict(public_contract.parameter_mappings),
    )
    route = _route("text-only-route", "google", "official", priority=1, contract=route_contract)
    pools = {"official": _pool("official", "google", "official", ("nano-banana",))}
    selector = RouteSelector()
    params = {"size": "1024x1024", "output_count": 1}

    assert len(selector.candidates(_model(operation_contracts=(public_contract,)), "image.edit", params, {"prompt": ("text",)}, (route,), pools)) == 1
    assert selector.candidates(_model(operation_contracts=(public_contract,)), "image.edit", params, _facts(), (route,), pools) == ()


def test_selector_excludes_inactive_wrong_missing_empty_and_unhealthy_routes() -> None:
    base = _route("base-route", "google", "official", priority=1)
    routes = (
        replace(base, route_id="disabled-route", enabled=False),
        replace(base, route_id="archived-route", enabled=False, archived_at="2026-08-12T00:00:00+00:00"),
        replace(base, route_id="wrong-model", model_id="other-model"),
        replace(base, route_id="missing-pool", credential_pool_ref="missing"),
        replace(base, route_id="empty-pool", credential_pool_ref="empty"),
        replace(base, route_id="unhealthy-route"),
        base,
    )
    pools = {
        "official": _pool("official", "google", "official", ("nano-banana",)),
        "empty": replace(_pool("empty", "google", "official", ("nano-banana",)), keys=()),
    }

    candidates = RouteSelector(unhealthy_route_ids=frozenset({"unhealthy-route"})).candidates(
        _model(), "image.edit", {"size": "1024x1024", "output_count": 1},
        _facts(), routes, pools,
    )

    assert tuple(item.route.route_id for item in candidates) == ("base-route",)
    assert RouteSelector().candidates(replace(_model(), enabled=False), "image.edit", {}, _facts(), (base,), pools) == ()
    assert RouteSelector().candidates(replace(_model(), enabled=False, archived_at="2026-08-12T00:00:00+00:00"), "image.edit", {}, _facts(), (base,), pools) == ()


def test_selector_orders_stably_by_priority_then_route_id() -> None:
    pools = {"official": _pool("official", "google", "official", ("nano-banana",))}
    routes = (
        _route("route-z", "google", "official", priority=3),
        _route("route-b", "google", "official", priority=1),
        _route("route-a", "google", "official", priority=1),
    )

    candidates = RouteSelector().candidates(
        _model(), "image.edit", {"size": "1024x1024", "output_count": 1}, _facts(), routes, pools,
    )

    assert tuple(item.route.route_id for item in candidates) == ("route-a", "route-b", "route-z")
    assert "secret-official" not in repr(candidates)

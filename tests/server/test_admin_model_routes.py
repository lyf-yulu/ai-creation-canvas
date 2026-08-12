from __future__ import annotations

from tests.server.test_admin_logical_models import clients, image_contract, model_body, route_body


def test_admin_route_round_trip_and_stale_revision(tmp_path) -> None:
    app, accounts, admin, user, headers, user_headers, pools = clients(tmp_path)
    del app, accounts, user, user_headers, pools
    assert admin.post("/api/v1/admin/logical-models", headers=headers, json=model_body()).status_code == 201
    created = admin.post("/api/v1/admin/logical-models/banana/routes", headers=headers, json=route_body())
    assert created.status_code == 201
    assert created.json()["revision"] == 1
    assert created.json()["credential_pool_ref"] == "t8-gemini"
    assert admin.get("/api/v1/admin/logical-models/banana/routes/banana-t8").status_code == 200

    update = route_body()
    update.update({"priority": 7, "revision": 1})
    changed = admin.put("/api/v1/admin/logical-models/banana/routes/banana-t8", headers=headers, json=update)
    assert changed.status_code == 200
    assert changed.json()["priority"] == 7 and changed.json()["revision"] == 2
    assert admin.put("/api/v1/admin/logical-models/banana/routes/banana-t8", headers=headers, json=update).status_code == 409
    assert admin.get("/api/v1/admin/logical-models/banana/routes").json()["routes"][0]["priority"] == 7


def test_route_compatibility_rejects_unsafe_or_cross_domain_config_without_write(tmp_path) -> None:
    app, accounts, admin, user, headers, user_headers, pools = clients(tmp_path)
    del accounts, user, user_headers, pools
    assert admin.post("/api/v1/admin/logical-models", headers=headers, json=model_body()).status_code == 201
    cases: list[dict[str, object]] = []
    cc = route_body("cc-route")
    cc["credential_pool_ref"] = "t8-cc"
    cases.append(cc)
    crossover = route_body("video-route")
    crossover.update({
        "provider_id": "google", "adapter_type": "ark", "credential_pool_ref": "seedance-official",
        "family": "seedance", "provider_model_name": "seedance-2-0",
        "operation_contracts": [{
            "operation": "video.generate", "input_ports": [{"port_id": "prompt", "media_type": "text", "min_items": 1, "max_items": 1}],
            "output_media_type": "video", "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False}, "parameter_mappings": {},
        }],
    })
    cases.append(crossover)
    mapping = route_body("mapping-route")
    mapping_contract = image_contract()
    mapping_contract["parameter_mappings"] = {"size": "unsafe_header", "output_count": "n"}
    mapping["operation_contracts"] = [mapping_contract]
    cases.append(mapping)
    unknown = route_body("unknown-route")
    unknown["adapter_type"] = "python.module"
    cases.append(unknown)
    for body in cases:
        response = admin.post("/api/v1/admin/logical-models/banana/routes", headers=headers, json=body)
        assert response.status_code == 400, (body["route_id"], response.text)
    app.state.canvas_store.update_provider_definition(
        app.state.canvas_store.provider_definition("t8star").__class__("t8star", "T8", "chiyun_openai_images", "https://t8.example", "unused", False, 1),
        expected_revision=1,
        actor_user_id="bootstrap",
    )
    disabled_provider = route_body("disabled-provider-route")
    response = admin.post("/api/v1/admin/logical-models/banana/routes", headers=headers, json=disabled_provider)
    assert response.status_code == 400
    assert app.state.canvas_store.list_model_routes(model_id="banana") == ()


def test_missing_pool_and_provider_with_routes_are_rejected(tmp_path) -> None:
    app, accounts, admin, user, headers, user_headers, pools = clients(tmp_path)
    del accounts, user, user_headers
    assert admin.post("/api/v1/admin/logical-models", headers=headers, json=model_body()).status_code == 201
    missing = route_body()
    missing["credential_pool_ref"] = "not-there"
    assert admin.post("/api/v1/admin/logical-models/banana/routes", headers=headers, json=missing).status_code == 400
    assert admin.post("/api/v1/admin/logical-models/banana/routes", headers=headers, json=route_body()).status_code == 201
    blocked = admin.delete("/api/v1/admin/model-registry/providers/t8star?revision=1", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["references"] == {"route": 1}


def test_route_configuration_validation_is_secret_free_and_has_no_runtime_side_effects(tmp_path) -> None:
    app, accounts, admin, user, headers, user_headers, pools = clients(tmp_path)
    del accounts, admin, user, headers, user_headers, pools
    from ai_creation_canvas.api.admin import ModelRouteCreate, _route_from_body

    definition = _route_from_body(ModelRouteCreate.model_validate(route_body()))
    app.state.managed_routing_runtime.adapter_factory.validate_route(definition)

    assert not (tmp_path / "data" / "chiyun-results").exists()
    assert not (tmp_path / "data" / "ark-results").exists()


def test_admin_can_create_and_edit_a_disabled_route_without_enabling_it(tmp_path) -> None:
    app, accounts, admin, user, headers, user_headers, pools = clients(tmp_path)
    del app, accounts, user, user_headers, pools
    assert admin.post("/api/v1/admin/logical-models", headers=headers, json=model_body()).status_code == 201
    body = route_body()
    body["enabled"] = False

    created = admin.post("/api/v1/admin/logical-models/banana/routes", headers=headers, json=body)

    assert created.status_code == 201
    assert created.json()["enabled"] is False
    body.update({"revision": 1, "priority": 5})
    updated = admin.put("/api/v1/admin/logical-models/banana/routes/banana-t8", headers=headers, json=body)
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False and updated.json()["priority"] == 5


def test_route_update_cannot_switch_lifecycle_state_or_immutable_ids(tmp_path) -> None:
    app, accounts, admin, user, headers, user_headers, pools = clients(tmp_path)
    del app, accounts, user, user_headers, pools
    admin.post("/api/v1/admin/logical-models", headers=headers, json=model_body())
    admin.post("/api/v1/admin/logical-models/banana/routes", headers=headers, json=route_body())
    changed_state = route_body()
    changed_state.update({"revision": 1, "enabled": False})
    changed_id = route_body("different-route")
    changed_id["revision"] = 1

    assert admin.put("/api/v1/admin/logical-models/banana/routes/banana-t8", headers=headers, json=changed_state).status_code == 400
    assert admin.put("/api/v1/admin/logical-models/banana/routes/banana-t8", headers=headers, json=changed_id).status_code == 400
    stored = admin.get("/api/v1/admin/logical-models/banana/routes/banana-t8").json()
    assert stored["enabled"] is True and stored["revision"] == 1

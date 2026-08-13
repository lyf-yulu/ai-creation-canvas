from __future__ import annotations

from tests.server.test_admin_logical_models import clients, image_contract, model_body, route_body, video_contract


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
        "provider_id": "ark", "adapter_type": "ark", "credential_pool_ref": "seedance-official",
        "family": "seedance", "provider_model_name": "doubao-seedance-2-5-260628",
        "operation_contracts": [video_contract()],
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
        app.state.canvas_store.provider_definition("chiyun-banana").__class__("chiyun-banana", "Chiyun Banana", "chiyun_gemini_images", "https://chiyun.work", "unused", False, 1),
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
    blocked = admin.delete("/api/v1/admin/model-registry/providers/chiyun-banana?revision=1", headers=headers)
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


def test_stale_route_revision_wins_before_current_pool_provider_or_template_validation(tmp_path) -> None:
    app, accounts, admin, user, headers, user_headers, pools = clients(tmp_path)
    del accounts, user, user_headers, pools
    admin.post("/api/v1/admin/logical-models", headers=headers, json=model_body())
    admin.post("/api/v1/admin/logical-models/banana/routes", headers=headers, json=route_body())
    update = route_body()
    update.update({"revision": 1, "priority": 7})
    assert admin.put("/api/v1/admin/logical-models/banana/routes/banana-t8", headers=headers, json=update).status_code == 200
    app.state.canvas_store.update_provider_definition(
        app.state.canvas_store.provider_definition("chiyun-banana").__class__("chiyun-banana", "Chiyun Banana", "chiyun_gemini_images", "https://chiyun.work", "unused", False, 1),
        expected_revision=1,
        actor_user_id="bootstrap",
    )
    stale = route_body()
    stale.update({"revision": 1, "credential_pool_ref": "missing-pool"})

    response = admin.put("/api/v1/admin/logical-models/banana/routes/banana-t8", headers=headers, json=stale)

    assert response.status_code == 409
    assert response.json()["code"] == "REVISION_CONFLICT"


def test_enable_reloads_removed_pool_and_makes_no_route_mutation_or_success_audit(tmp_path) -> None:
    app, accounts, admin, user, headers, user_headers, pools = clients(tmp_path)
    del accounts, user, user_headers
    assert admin.post("/api/v1/admin/logical-models", headers=headers, json=model_body()).status_code == 201
    body = route_body()
    body["enabled"] = False
    assert admin.post("/api/v1/admin/logical-models/banana/routes", headers=headers, json=body).status_code == 201
    before = tuple(app.state.canvas_store.admin_audit_events())
    pools.pop("t8-gemini")

    response = admin.post("/api/v1/admin/logical-models/banana/routes/banana-t8/enable", headers=headers, json={"revision": 1})

    assert response.status_code in {400, 409}
    stored = app.state.canvas_store.model_route("banana-t8")
    assert stored is not None and stored.enabled is False and stored.revision == 1
    assert tuple(app.state.canvas_store.admin_audit_events()) == before


def test_route_write_rejects_each_tampered_preset_identity_field(tmp_path) -> None:
    app, accounts, admin, user, headers, user_headers, pools = clients(tmp_path)
    del app, accounts, user, user_headers, pools
    assert admin.post("/api/v1/admin/logical-models", headers=headers, json=model_body()).status_code == 201
    base = route_body()
    base["enabled"] = False
    assert admin.post(
        "/api/v1/admin/logical-models/banana/routes", headers=headers,
        json={**base, "route_id": "valid-disabled"},
    ).status_code == 201
    cases = {
        "provider_id": "unknown",
        "provider_model_name": "gemini-2.5-flash-image-preview",
        "adapter_type": "ark",
        "family": "gpt-image",
    }
    for field, value in cases.items():
        body = {**base, "route_id": f"tampered-{field}", field: value}
        assert admin.post("/api/v1/admin/logical-models/banana/routes", headers=headers, json=body).status_code == 400
        update = {**base, "route_id": "valid-disabled", "revision": 1, field: value}
        assert admin.put("/api/v1/admin/logical-models/banana/routes/valid-disabled", headers=headers, json=update).status_code == 400
    contract = image_contract()
    contract["parameter_mappings"] = {"size": "size", "output_count": "images"}
    assert admin.post(
        "/api/v1/admin/logical-models/banana/routes", headers=headers,
        json={**base, "route_id": "tampered-contract", "operation_contracts": [contract]},
    ).status_code == 400
    assert admin.put(
        "/api/v1/admin/logical-models/banana/routes/valid-disabled", headers=headers,
        json={**base, "route_id": "valid-disabled", "revision": 1, "operation_contracts": [contract]},
    ).status_code == 400

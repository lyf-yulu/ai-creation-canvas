from __future__ import annotations

import json
import threading

from tests.server.test_admin_logical_models import clients, model_body, route_body


def test_explicit_model_and_route_lifecycle_and_unused_delete(tmp_path) -> None:
    app, accounts, admin, user, headers, user_headers, pools = clients(tmp_path)
    del accounts, user, user_headers, pools
    assert admin.post("/api/v1/admin/logical-models", headers=headers, json=model_body()).status_code == 201
    assert admin.post("/api/v1/admin/logical-models/banana/routes", headers=headers, json=route_body()).status_code == 201
    disabled = admin.post("/api/v1/admin/logical-models/banana/routes/banana-t8/disable", headers=headers, json={"revision": 1})
    assert disabled.status_code == 200 and disabled.json()["enabled"] is False
    archived = admin.post("/api/v1/admin/logical-models/banana/routes/banana-t8/archive", headers=headers, json={"revision": 2})
    assert archived.status_code == 200 and archived.json()["archived_at"]
    restored = admin.post("/api/v1/admin/logical-models/banana/routes/banana-t8/restore", headers=headers, json={"revision": 3})
    assert restored.status_code == 200 and restored.json()["archived_at"] is None and restored.json()["enabled"] is False
    assert admin.delete("/api/v1/admin/logical-models/banana/routes/banana-t8?revision=4", headers=headers).status_code == 204

    disabled_model = admin.post("/api/v1/admin/logical-models/banana/disable", headers=headers, json={"revision": 1})
    assert disabled_model.status_code == 200 and disabled_model.json()["enabled"] is False
    archived_model = admin.post("/api/v1/admin/logical-models/banana/archive", headers=headers, json={"revision": 2})
    assert archived_model.status_code == 200 and archived_model.json()["archived_at"]
    restored_model = admin.post("/api/v1/admin/logical-models/banana/restore", headers=headers, json={"revision": 3})
    assert restored_model.status_code == 200 and restored_model.json()["enabled"] is False
    assert admin.delete("/api/v1/admin/logical-models/banana?revision=4", headers=headers).status_code == 204

    actions = [event["action"] for event in app.state.canvas_store.admin_audit_events() if event["target_type"] in {"logical_model", "model_route"}]
    assert actions == [
        "logical_model.create", "model_route.create", "model_route.disable", "model_route.archive",
        "model_route.restore", "model_route.delete", "logical_model.disable", "logical_model.archive",
        "logical_model.restore", "logical_model.delete",
    ]


def test_referenced_delete_returns_counts_only_and_purge_keeps_minimal_stub(tmp_path) -> None:
    app, accounts, admin, user, headers, user_headers, pools = clients(tmp_path)
    del accounts, user, user_headers, pools
    admin.post("/api/v1/admin/logical-models", headers=headers, json=model_body())
    admin.post("/api/v1/admin/logical-models/banana/routes", headers=headers, json=route_body())
    reservation = app.state.canvas_store.reserve_job(
        user_id="history-user", job_id="history-job", service_id="banana", operation="image.edit",
        idempotency_key="history-key", request_hash="a" * 64, logical_model_id="banana", logical_model_revision=1,
    )
    app.state.canvas_store.record_routing_snapshot(
        "history-job", reservation.job["submission_token"], logical_model_id="banana", logical_model_revision=1,
        route_id="banana-t8", route_revision=1, pool_revision_digest="b" * 64,
        key_fingerprint="c" * 64, route_snapshot_json="{}",
    )

    blocked = admin.delete("/api/v1/admin/logical-models/banana/routes/banana-t8?revision=1", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["references"] == {"job": 1}
    assert "history-job" not in blocked.text and "history-user" not in blocked.text
    purged = admin.post("/api/v1/admin/logical-models/banana/routes/banana-t8/purge-runtime", headers=headers, json={"revision": 1})
    assert purged.status_code == 200
    encoded = json.dumps(purged.json())
    assert "provider" not in encoded and "pool" not in encoded and "adapter" not in encoded and "family" not in encoded
    assert purged.json()["route_id"] == "banana-t8"

    model_blocked = admin.delete("/api/v1/admin/logical-models/banana?revision=1", headers=headers)
    assert model_blocked.status_code == 409
    assert set(model_blocked.json()["references"]) <= {"job", "access", "assignment", "route"}
    model_purged = admin.post("/api/v1/admin/logical-models/banana/purge-runtime", headers=headers, json={"revision": 1})
    assert model_purged.status_code == 200
    assert set(model_purged.json()) <= {"model_id", "display_name", "modality", "enabled", "archived_at", "revision", "created_at", "updated_at"}


def test_concurrent_same_revision_updates_have_one_winner_and_one_conflict(tmp_path) -> None:
    app, accounts, admin, user, headers, user_headers, pools = clients(tmp_path)
    del accounts, admin, user, user_headers, pools
    from ai_creation_canvas.model_routing import LogicalModelDefinition, RevisionConflict
    from ai_creation_canvas.model_registry import OperationContract

    body = model_body()
    first = LogicalModelDefinition(
        body["model_id"], body["display_name"], body["introduction"], body["modality"],
        tuple(OperationContract.from_dict(item) for item in body["operation_contracts"]), True, None, 1,
    )
    app.state.canvas_store.create_logical_model(first, actor_user_id="admin")
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def update(name: str) -> None:
        candidate = LogicalModelDefinition(first.model_id, name, first.introduction, first.modality, first.operation_contracts, True, None, 1)
        barrier.wait()
        try:
            app.state.canvas_store.update_logical_model(candidate, expected_revision=1, actor_user_id="admin")
            outcomes.append("success")
        except RevisionConflict:
            outcomes.append("conflict")

    threads = [threading.Thread(target=update, args=(name,)) for name in ("First", "Second")]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sorted(outcomes) == ["conflict", "success"]
    events = [event for event in app.state.canvas_store.admin_audit_events() if event["action"] == "logical_model.update"]
    assert len(events) == 1


def test_failed_validation_or_conflict_does_not_write_success_audit(tmp_path) -> None:
    app, accounts, admin, user, headers, user_headers, pools = clients(tmp_path)
    del accounts, user, user_headers, pools
    admin.post("/api/v1/admin/logical-models", headers=headers, json=model_body())
    before = tuple(app.state.canvas_store.admin_audit_events())
    bad = route_body()
    bad["credential_pool_ref"] = "t8-cc"
    assert admin.post("/api/v1/admin/logical-models/banana/routes", headers=headers, json=bad).status_code == 400
    stale = model_body()
    stale["revision"] = 99
    assert admin.put("/api/v1/admin/logical-models/banana", headers=headers, json=stale).status_code == 409
    assert tuple(app.state.canvas_store.admin_audit_events()) == before


def test_parent_runtime_purge_audits_each_referenced_child_route(tmp_path) -> None:
    app, accounts, admin, user, headers, user_headers, pools = clients(tmp_path)
    del accounts, user, user_headers, pools
    admin.post("/api/v1/admin/logical-models", headers=headers, json=model_body())
    admin.post("/api/v1/admin/logical-models/banana/routes", headers=headers, json=route_body())
    reservation = app.state.canvas_store.reserve_job(
        user_id="history-user", job_id="parent-purge-job", service_id="banana", operation="image.edit",
        idempotency_key="parent-purge-key", request_hash="d" * 64, logical_model_id="banana", logical_model_revision=1,
    )
    app.state.canvas_store.record_routing_snapshot(
        "parent-purge-job", reservation.job["submission_token"], logical_model_id="banana", logical_model_revision=1,
        route_id="banana-t8", route_revision=1, pool_revision_digest="e" * 64,
        key_fingerprint="f" * 64, route_snapshot_json="{}",
    )

    response = admin.post("/api/v1/admin/logical-models/banana/purge-runtime", headers=headers, json={"revision": 1})

    assert response.status_code == 200
    actions = [event["action"] for event in app.state.canvas_store.admin_audit_events()]
    assert actions[-2:] == ["model_route.purge_runtime", "logical_model.purge_runtime"]

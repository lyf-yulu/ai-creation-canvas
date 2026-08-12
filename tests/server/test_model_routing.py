from __future__ import annotations

from dataclasses import replace
import json
import sqlite3

import pytest

from ai_creation_canvas.credential_pools import CredentialKey, CredentialPool
from ai_creation_canvas.domain.models import ModelInputPort, ModelOperation
from ai_creation_canvas.model_registry import OperationContract
from ai_creation_canvas.model_routing import (
    LogicalModelDefinition,
    ModelRouteDefinition,
    ObjectReferenced,
    RouteCompatibility,
    validate_route_model,
    validate_route_pool,
)
from ai_creation_canvas.storage.sqlite import CanvasStore


def image_edit_contract(*, mappings: dict[str, str] | None = None) -> OperationContract:
    schema = {
        "type": "object",
        "properties": {
            "size": {"type": "string", "enum": ["1024x1024"]},
            "output_count": {"type": "integer", "minimum": 1, "maximum": 4},
        },
        "additionalProperties": False,
    }
    return OperationContract(
        ModelOperation.IMAGE_EDIT,
        (
            ModelInputPort("prompt", "text", 1, 1),
            ModelInputPort("reference_images", "image", 1, 10),
        ),
        "image",
        schema,
        mappings or {"size": "size", "output_count": "n"},
    )


def logical_model(**changes: object) -> LogicalModelDefinition:
    values: dict[str, object] = {
        "model_id": "nano-banana-edit",
        "display_name": "Nano Banana Edit",
        "introduction": "Edit an image from references.",
        "modality": "image",
        "operation_contracts": (image_edit_contract(),),
        "enabled": True,
        "archived_at": None,
        "revision": 1,
    }
    values.update(changes)
    return LogicalModelDefinition(**values)  # type: ignore[arg-type]


def model_route(**changes: object) -> ModelRouteDefinition:
    values: dict[str, object] = {
        "route_id": "nano-banana-t8",
        "model_id": "nano-banana-edit",
        "provider_id": "t8star",
        "provider_model_name": "gemini-2.5-flash-image-preview",
        "adapter_type": "chiyun_openai_images",
        "credential_pool_ref": "t8-gemini",
        "family": "nano-banana",
        "operation_contracts": (image_edit_contract(),),
        "priority": 20,
        "max_concurrency": 8,
        "enabled": True,
        "archived_at": None,
        "revision": 1,
    }
    values.update(changes)
    return ModelRouteDefinition(**values)  # type: ignore[arg-type]


def pool(
    *,
    pool_id: str = "t8-gemini",
    provider: str = "t8star",
    group: str = "gemini",
    families: tuple[str, ...] = ("nano-banana",),
) -> CredentialPool:
    return CredentialPool(
        pool_id=pool_id,
        provider_id=provider,
        group=group,
        allowed_families=families,
        keys=(CredentialKey("fake-key", "test-only-secret", 1),),
        revision_digest="a" * 64,
    )


def test_logical_model_rejects_cross_modality_operation() -> None:
    video_contract = OperationContract(
        ModelOperation.VIDEO_GENERATE,
        (ModelInputPort("prompt", "text", 1, 1),),
        "video",
        {"type": "object", "properties": {}, "additionalProperties": False},
        {},
    )

    with pytest.raises(ValueError, match="modality"):
        logical_model(operation_contracts=(video_contract,))


def test_model_contracts_bound_schema_and_require_every_parameter_mapping() -> None:
    properties = {f"parameter_{index}": {"type": "string"} for index in range(65)}
    mappings = {name: name for name in properties}
    oversized = OperationContract(
        ModelOperation.IMAGE_EDIT,
        (ModelInputPort("prompt", "text", 1, 1),),
        "image",
        {"type": "object", "properties": properties, "additionalProperties": False},
        mappings,
    )

    with pytest.raises(ValueError, match="bounded"):
        logical_model(operation_contracts=(oversized,))

    with pytest.raises(ValueError, match="each parameter requires one mapping"):
        image_edit_contract(mappings={"size": "size"})


def test_route_must_be_compatible_with_logical_model_contract() -> None:
    incompatible = model_route(
        operation_contracts=(
            OperationContract(
                ModelOperation.IMAGE_EDIT,
                (ModelInputPort("prompt", "text", 1, 1),),
                "image",
                {
                    "type": "object",
                    "properties": {"size": {"type": "string", "enum": ["512x512"]}},
                    "additionalProperties": False,
                },
                {"size": "size"},
            ),
        )
    )

    with pytest.raises(ValueError, match="contract"):
        validate_route_model(incompatible, logical_model())


def test_route_cannot_omit_required_logical_model_input_port(tmp_path) -> None:
    schema = {
        "type": "object",
        "properties": {
            "size": {"type": "string", "enum": ["1024x1024"]},
            "output_count": {"type": "integer", "minimum": 1, "maximum": 4},
        },
        "additionalProperties": False,
    }
    mappings = {"size": "size", "output_count": "n"}
    route_contract = OperationContract(
        ModelOperation.IMAGE_EDIT,
        (ModelInputPort("prompt", "text", 1, 1),),
        "image",
        schema,
        mappings,
    )
    store = CanvasStore(tmp_path)
    store.create_logical_model(logical_model())

    with pytest.raises(ValueError, match="required input ports"):
        store.create_model_route(model_route(operation_contracts=(route_contract,)))


def test_route_may_omit_optional_logical_model_input_port(tmp_path) -> None:
    schema = {
        "type": "object",
        "properties": {
            "size": {"type": "string", "enum": ["1024x1024"]},
            "output_count": {"type": "integer", "minimum": 1, "maximum": 4},
        },
        "additionalProperties": False,
    }
    mappings = {"size": "size", "output_count": "n"}
    optional_model_contract = OperationContract(
        ModelOperation.IMAGE_EDIT,
        (
            ModelInputPort("prompt", "text", 1, 1),
            ModelInputPort("reference_images", "image", 0, 10),
        ),
        "image",
        schema,
        mappings,
    )
    route_contract = OperationContract(
        ModelOperation.IMAGE_EDIT,
        (ModelInputPort("prompt", "text", 1, 1),),
        "image",
        schema,
        mappings,
    )
    store = CanvasStore(tmp_path)
    store.create_logical_model(logical_model(operation_contracts=(optional_model_contract,)))

    created = store.create_model_route(model_route(operation_contracts=(route_contract,)))

    assert tuple(port.port_id for port in created.operation_contracts[0].input_ports) == ("prompt",)


@pytest.mark.parametrize(
    ("candidate", "message"),
    [
        (pool(pool_id="other-pool"), "pool"),
        (pool(provider="other-provider"), "provider"),
        (pool(group="cc", families=("claude",)), "family"),
    ],
)
def test_route_pool_requires_exact_pool_provider_and_family(candidate: CredentialPool, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_route_pool(model_route(), candidate)


def test_cc_pool_cannot_back_nano_banana_route() -> None:
    with pytest.raises(ValueError, match="family"):
        validate_route_pool(
            model_route(),
            pool(provider="t8star", group="cc", families=("claude",)),
        )


def test_compatible_route_projection_is_bounded_and_secret_free() -> None:
    compatibility = RouteCompatibility(
        route_id="nano-banana-t8",
        operation=ModelOperation.IMAGE_EDIT,
        provider_id="t8star",
        pool_id="t8-gemini",
        priority=20,
    )

    assert compatibility.operation is ModelOperation.IMAGE_EDIT
    assert "test-only-secret" not in repr(compatibility)


def test_archived_domain_objects_must_be_disabled() -> None:
    with pytest.raises(ValueError, match="archived"):
        logical_model(archived_at="2026-08-12T00:00:00+00:00", enabled=True)
    with pytest.raises(ValueError, match="archived"):
        model_route(archived_at="2026-08-12T00:00:00+00:00", enabled=True)


def test_store_requires_exact_revisions_and_restore_stays_disabled(tmp_path) -> None:
    store = CanvasStore(tmp_path)
    created = store.create_logical_model(logical_model())

    with pytest.raises(ValueError, match="revision conflict"):
        store.update_logical_model(replace(created, display_name="Changed"), expected_revision=2)

    updated = store.update_logical_model(
        replace(created, display_name="Changed"), expected_revision=1
    )
    assert updated.display_name == "Changed" and updated.revision == 2

    archived = store.archive_logical_model(updated.model_id, expected_revision=2)
    assert archived.enabled is False and archived.archived_at is not None and archived.revision == 3

    restored = store.restore_logical_model(archived.model_id, expected_revision=3)
    assert restored.enabled is False and restored.archived_at is None and restored.revision == 4


def test_route_lifecycle_checks_model_contract_and_revision(tmp_path) -> None:
    store = CanvasStore(tmp_path)
    store.create_logical_model(logical_model())

    with pytest.raises(ValueError, match="contract"):
        store.create_model_route(
            model_route(
                operation_contracts=(
                    OperationContract(
                        ModelOperation.IMAGE_GENERATE,
                        (ModelInputPort("prompt", "text", 1, 1),),
                        "image",
                        {"type": "object", "properties": {}, "additionalProperties": False},
                        {},
                    ),
                )
            )
        )

    created = store.create_model_route(model_route())
    with pytest.raises(ValueError, match="revision conflict"):
        store.archive_model_route(created.route_id, expected_revision=2)
    archived = store.archive_model_route(created.route_id, expected_revision=1)
    restored = store.restore_model_route(archived.route_id, expected_revision=2)
    assert restored.enabled is False and restored.archived_at is None and restored.revision == 3


def test_unused_routes_and_models_are_physically_deleted(tmp_path) -> None:
    store = CanvasStore(tmp_path)
    store.create_logical_model(logical_model())
    store.create_model_route(model_route())

    assert store.delete_model_route("nano-banana-t8", expected_revision=1).deleted is True
    assert store.delete_logical_model("nano-banana-edit", expected_revision=1).deleted is True
    assert store.logical_model_references("nano-banana-edit") == ()


def test_referenced_route_is_purged_to_non_executable_audit_stub(tmp_path) -> None:
    store = CanvasStore(tmp_path)
    store.create_logical_model(logical_model())
    store.create_model_route(model_route())
    with sqlite3.connect(store.database) as db:
        db.execute(
            "INSERT INTO canvas_jobs(id,user_id,service_id,operation,status,idempotency_key,request_hash,route_id,created_at,updated_at) "
            "VALUES ('job-1','user-1','svc','image.edit','succeeded','key-1','hash','nano-banana-t8','now','now')"
        )

    assert store.route_references("nano-banana-t8") == ("job:job-1",)
    with pytest.raises(ObjectReferenced):
        store.delete_model_route("nano-banana-t8", expected_revision=1)

    stub = store.purge_model_route_runtime("nano-banana-t8", expected_revision=1)
    encoded = json.dumps(stub.audit_projection(), sort_keys=True)
    assert stub.enabled is False and stub.archived_at is not None
    for forbidden in (
        "credential_pool_ref",
        "provider_model_name",
        "provider_id",
        "adapter_type",
        "family",
    ):
        assert forbidden not in encoded

    with sqlite3.connect(store.database) as db:
        row = db.execute(
            "SELECT provider_id,provider_model_name,adapter_type,credential_pool_ref,family,operation_contracts_json "
            "FROM canvas_model_routes WHERE route_id='nano-banana-t8'"
        ).fetchone()
    assert row == (None, None, None, None, None, "[]")
    with pytest.raises(ValueError, match="runtime was purged"):
        store.purge_model_route_runtime("nano-banana-t8", expected_revision=2)

    with sqlite3.connect(store.database) as db:
        db.execute("DELETE FROM canvas_jobs WHERE id='job-1'")
    assert store.route_references("nano-banana-t8") == ()
    with pytest.raises(ObjectReferenced, match="audit stub"):
        store.delete_model_route("nano-banana-t8", expected_revision=2)
    with pytest.raises(ValueError, match="already exists"):
        store.create_model_route(model_route())


def test_migrated_legacy_route_recognizes_jobs_created_before_route_ids(tmp_path) -> None:
    store = CanvasStore(tmp_path)
    store.create_logical_model(logical_model())
    store.create_model_route(model_route(route_id="legacy-nano-banana-edit"))
    with sqlite3.connect(store.database) as db:
        db.execute(
            "INSERT INTO canvas_jobs(id,user_id,service_id,operation,status,idempotency_key,request_hash,model_id,route_id,created_at,updated_at) "
            "VALUES ('old-job','user-1','svc','image.edit','succeeded','old-key','hash','nano-banana-edit',NULL,'now','now')"
        )

    assert store.route_references("legacy-nano-banana-edit") == ("job:old-job",)
    with pytest.raises(ObjectReferenced):
        store.delete_model_route("legacy-nano-banana-edit", expected_revision=1)


def test_referenced_model_purge_physically_deletes_unreferenced_child_route(tmp_path) -> None:
    store = CanvasStore(tmp_path)
    store.create_logical_model(logical_model())
    store.create_model_route(model_route())
    with sqlite3.connect(store.database) as db:
        db.execute(
            "INSERT INTO canvas_jobs(id,user_id,service_id,operation,status,idempotency_key,request_hash,model_id,created_at,updated_at) "
            "VALUES ('job-2','user-1','svc','image.edit','succeeded','key-2','hash','nano-banana-edit','now','now')"
        )

    assert store.logical_model_references("nano-banana-edit") == (
        "job:job-2",
        "route:nano-banana-t8",
    )
    with pytest.raises(ObjectReferenced):
        store.delete_logical_model("nano-banana-edit", expected_revision=1)

    stub = store.purge_logical_model_runtime("nano-banana-edit", expected_revision=1)
    assert stub.enabled is False and stub.archived_at is not None
    encoded = json.dumps(stub.audit_projection(), sort_keys=True)
    assert "credential_pool_ref" not in encoded and "operation_contracts" not in encoded

    with sqlite3.connect(store.database) as db:
        logical = db.execute(
            "SELECT introduction,operation_contracts_json,enabled,runtime_purged FROM canvas_logical_models WHERE model_id=?",
            ("nano-banana-edit",),
        ).fetchone()
        route = db.execute(
            "SELECT provider_id,provider_model_name,credential_pool_ref,enabled,runtime_purged FROM canvas_model_routes WHERE route_id=?",
            ("nano-banana-t8",),
        ).fetchone()
    assert logical == ("", "[]", 0, 1)
    assert route is None
    with pytest.raises(ValueError, match="runtime was purged"):
        store.purge_logical_model_runtime("nano-banana-edit", expected_revision=2)

    with sqlite3.connect(store.database) as db:
        db.execute("DELETE FROM canvas_jobs WHERE id='job-2'")
    assert store.logical_model_references("nano-banana-edit") == ()
    with pytest.raises(ObjectReferenced, match="audit stub"):
        store.delete_logical_model("nano-banana-edit", expected_revision=2)
    with pytest.raises(ValueError, match="already exists"):
        store.create_logical_model(logical_model())

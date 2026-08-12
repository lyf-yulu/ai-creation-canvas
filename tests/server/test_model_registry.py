from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ai_creation_canvas.domain.models import ModelInputPort, ModelOperation
from ai_creation_canvas.model_registry import (
    GovernedModelDefinition,
    ModelModality,
    OperationContract,
    ProviderDefinition,
)
from ai_creation_canvas.storage.sqlite import CanvasStore


def _provider(**changes: object) -> ProviderDefinition:
    values: dict[str, object] = {
        "provider_id": "chiyun",
        "display_name": "Chiyun",
        "adapter_type": "chiyun_openai_images",
        "base_url": "https://chiyun.example",
        "credential_ref": "chiyun-primary",
        "enabled": True,
        "revision": 1,
    }
    values.update(changes)
    return ProviderDefinition(**values)  # type: ignore[arg-type]


def _model(**changes: object) -> GovernedModelDefinition:
    contract = OperationContract(
        operation=ModelOperation.IMAGE_EDIT,
        input_ports=(
            ModelInputPort("prompt", "text", 1, 1),
            ModelInputPort("reference_images", "image", 1, 10),
        ),
        output_media_type="image",
        parameter_schema={
            "type": "object",
            "properties": {
                "size": {"type": "string", "enum": ["auto", "1024x1024"]},
                "output_count": {"type": "integer", "minimum": 1, "maximum": 4},
            },
            "required": ["size", "output_count"],
            "additionalProperties": False,
        },
        parameter_mappings={"size": "size", "output_count": "n"},
    )
    values: dict[str, object] = {
        "model_id": "chiyun-gpt-image-2",
        "provider_id": "chiyun",
        "provider_model_name": "gpt-image-2",
        "display_name": "GPT Image 2",
        "introduction": "Chiyun 多参考图编辑",
        "modality": ModelModality.IMAGE,
        "operation_contracts": (contract,),
        "enabled": True,
        "revision": 1,
    }
    values.update(changes)
    return GovernedModelDefinition(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"adapter_type": "dynamic.python.module"},
        {"base_url": "http://chiyun.example"},
        {"base_url": "https://user:pass@chiyun.example"},
        {"base_url": "https://chiyun.example/api/v1"},
        {"credential_ref": "secret/value"},
    ],
)
def test_provider_definition_rejects_unsafe_runtime_configuration(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _provider(**changes)


def test_provider_public_projection_excludes_runtime_secrets_and_origin() -> None:
    projection = _provider().public_projection()

    assert projection == {
        "provider_id": "chiyun",
        "display_name": "Chiyun",
        "enabled": True,
        "revision": 1,
    }
    assert "credential_ref" not in projection
    assert "base_url" not in projection
    assert "adapter_type" not in projection


def test_model_definition_records_one_isolated_image_edit_contract() -> None:
    model = _model()
    spec = model.model_spec("chiyun-service")

    assert spec.operations == (ModelOperation.IMAGE_EDIT,)
    assert [port.port_id for port in spec.input_ports] == ["prompt", "reference_images"]
    assert spec.parameter_mappings == {"size": "size", "output_count": "n"}
    projection = model.public_projection()
    assert projection["modality"] == "image"
    assert projection["operations"] == ["image.edit"]
    assert "provider_model_name" not in projection
    assert "parameter_mappings" not in str(projection)


def test_model_definition_rejects_cross_modality_and_ambiguous_contracts() -> None:
    video = OperationContract(
        operation=ModelOperation.VIDEO_GENERATE,
        input_ports=(ModelInputPort("prompt", "text", 1, 1),),
        output_media_type="video",
        parameter_schema={"type": "object", "properties": {}, "additionalProperties": False},
        parameter_mappings={},
    )
    with pytest.raises(ValueError):
        _model(operation_contracts=(video,))

    duplicate = _model().operation_contracts[0]
    with pytest.raises(ValueError):
        _model(operation_contracts=(duplicate, duplicate))


def test_store_round_trips_revisions_access_and_audit(tmp_path: Path) -> None:
    store = CanvasStore(tmp_path / "data")
    store.create_user(
        user_id="user-1",
        username_normalized="user-1",
        display_name="User 1",
        password_hash="hash",
        role="user",
        must_change_password=False,
    )

    saved_provider = store.create_provider_definition(_provider(), actor_user_id="admin-1")
    saved_model = store.create_model_definition(_model(), actor_user_id="admin-1")
    assert saved_provider == _provider()
    assert saved_model == _model()
    assert store.provider_definition("chiyun") == _provider()
    assert store.model_definition("chiyun-gpt-image-2") == _model()

    with pytest.raises(ValueError):
        store.update_provider_definition(replace(_provider(), revision=1, display_name="Changed"), expected_revision=0, actor_user_id="admin-1")
    changed = store.update_provider_definition(replace(_provider(), display_name="Changed"), expected_revision=1, actor_user_id="admin-1")
    assert changed.revision == 2
    assert changed.display_name == "Changed"

    store.grant_model_access("user-1", "chiyun-gpt-image-2", actor_user_id="admin-1")
    assert store.governed_assigned_models("user-1") == ("chiyun-gpt-image-2",)
    store.revoke_model_access("user-1", "chiyun-gpt-image-2", actor_user_id="admin-1")
    assert store.governed_assigned_models("user-1") == ()

    events = store.admin_audit_events()
    assert [event["action"] for event in events] == [
        "provider.create",
        "model.create",
        "provider.update",
        "model_access.grant",
        "model_access.revoke",
    ]
    assert all(event["actor_user_id"] == "admin-1" for event in events)


def test_store_rejects_duplicate_ids_and_missing_foreign_keys(tmp_path: Path) -> None:
    store = CanvasStore(tmp_path / "data")
    store.create_provider_definition(_provider(), actor_user_id="admin")
    with pytest.raises(ValueError):
        store.create_provider_definition(_provider(), actor_user_id="admin")
    with pytest.raises(KeyError):
        store.create_model_definition(_model(provider_id="missing"), actor_user_id="admin")
    with pytest.raises(KeyError):
        store.grant_model_access("missing-user", "missing-model", actor_user_id="admin")

from __future__ import annotations

from pathlib import Path

import pytest

from ai_creation_canvas.comfy import (
    WorkflowFormat,
    WorkflowValidationError,
    export_workflow,
    parse_workflow_json,
)
from ai_creation_canvas.comfy.library import ComfyWorkflowLibrary
from ai_creation_canvas.storage.sqlite import CanvasStore


FIXTURE = Path(__file__).parents[1] / "fixtures" / "comfy" / "core-load-save-workflow.json"
API_BYTES = (
    b'{"2":{"class_type":"SaveImage","inputs":{"images":["1",0]}},'
    b'"1":{"class_type":"LoadImage","inputs":{}}}'
)


def test_revisions_are_immutable_and_enabled_assignment_is_owner_scoped(tmp_path: Path) -> None:
    """Returning disabled or another user's template would be an access-control bug."""
    store = CanvasStore(tmp_path)
    library = ComfyWorkflowLibrary(store)
    editor = parse_workflow_json(FIXTURE.read_bytes())
    template = library.create_template("Core image", "comfy-local", editor, actor_user_id="admin")
    revised = library.add_revision(
        template.workflow_id,
        parse_workflow_json(API_BYTES),
        expected_revision=1,
        actor_user_id="admin",
    )

    assert template.revision == 1 and revised.revision == 2
    assert library.assigned_list("user-a") == ()
    library.replace_assignments("user-a", (template.workflow_id,), actor_user_id="admin")
    assert library.assigned_list("user-a") == ()

    enabled = library.set_lifecycle(
        template.workflow_id, enabled=True, expected_revision=2, actor_user_id="admin"
    )
    assert enabled.revision == 3 and enabled.enabled is True
    assert [item.workflow_id for item in library.assigned_list("user-a")] == [template.workflow_id]
    assert library.assigned_list("user-b") == ()
    assert library.export_revision(template.workflow_id, 1, WorkflowFormat.EDITOR) == export_workflow(
        editor, WorkflowFormat.EDITOR
    )
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_FORMAT_UNAVAILABLE"):
        library.export_revision(template.workflow_id, 1, WorkflowFormat.API)


def test_duplicate_format_checksum_does_not_create_a_revision_or_audit_event(tmp_path: Path) -> None:
    """Duplicated uploads must not fabricate history or administrator audit activity."""
    store = CanvasStore(tmp_path)
    library = ComfyWorkflowLibrary(store)
    parsed = parse_workflow_json(FIXTURE.read_bytes())
    template = library.create_template("Core image", "comfy-local", parsed, actor_user_id="admin")
    before_events = store.admin_audit_events()

    with pytest.raises(WorkflowValidationError, match="WORKFLOW_DUPLICATE_REVISION"):
        library.add_revision(
            template.workflow_id, parsed, expected_revision=1, actor_user_id="admin"
        )

    assert library.admin_list()[0].revision == 1
    assert store.admin_audit_events() == before_events


def test_archiving_removes_an_assigned_workflow_and_rejects_new_assignment(tmp_path: Path) -> None:
    """An archived template must not remain reachable through an existing assignment."""
    library = ComfyWorkflowLibrary(CanvasStore(tmp_path))
    template = library.create_template(
        "Core image", "comfy-local", parse_workflow_json(FIXTURE.read_bytes()), actor_user_id="admin"
    )
    library.replace_assignments("user-a", (template.workflow_id,), actor_user_id="admin")
    enabled = library.set_lifecycle(
        template.workflow_id, enabled=True, expected_revision=1, actor_user_id="admin"
    )
    library.set_lifecycle(
        template.workflow_id, archived=True, expected_revision=enabled.revision, actor_user_id="admin"
    )

    assert library.assigned_list("user-a") == ()
    with pytest.raises(KeyError, match=template.workflow_id):
        library.replace_assignments("user-b", (template.workflow_id,), actor_user_id="admin")


def test_store_rejects_a_paired_editor_and_api_revision_with_different_inventory(tmp_path: Path) -> None:
    """A mismatched API attachment could execute a different graph than the previewed revision."""
    store = CanvasStore(tmp_path)
    editor = parse_workflow_json(FIXTURE.read_bytes())
    api = parse_workflow_json(
        b'{"1":{"class_type":"LoadImage","inputs":{}},'
        b'"3":{"class_type":"SaveImage","inputs":{"images":["1",0]}}}'
    )

    with pytest.raises(ValueError, match="WORKFLOW_PAIR_MISMATCH"):
        store.create_comfy_workflow(
            workflow_id="cw-pair",
            display_name="Pair mismatch",
            description="",
            service_id="comfy-local",
            source_filename="workflow.json",
            editor_json=export_workflow(editor, WorkflowFormat.EDITOR).decode(),
            api_json=export_workflow(api, WorkflowFormat.API).decode(),
            editor_checksum=editor.checksum,
            api_checksum=api.checksum,
            node_inventory_json="{}",
            dependency_inventory_json="{}",
            actor_user_id="admin",
        )


def test_comfy_prompt_owner_is_durable_and_cannot_be_reassigned_to_another_user(tmp_path: Path) -> None:
    store = CanvasStore(tmp_path)

    assert store.record_comfy_prompt_owner(
        service_id="comfy-local", prompt_id="prompt-1", user_id="owner-a", idempotency_key="idem-a"
    ) is True
    assert store.record_comfy_prompt_owner(
        service_id="comfy-local", prompt_id="prompt-1", user_id="owner-b", idempotency_key="idem-b"
    ) is False

    restored = CanvasStore(tmp_path)
    assert restored.comfy_prompt_owner("comfy-local", "prompt-1") == "owner-a"


def test_paired_revision_rejects_an_already_stored_api_checksum_without_side_effects(tmp_path: Path) -> None:
    """Checking only the new editor checksum would allow an API revision to be reused."""
    store = CanvasStore(tmp_path)
    editor = parse_workflow_json(FIXTURE.read_bytes())
    api = parse_workflow_json(API_BYTES)
    store.create_comfy_workflow(
        workflow_id="cw-paired",
        display_name="Paired duplicate",
        description="",
        service_id="comfy-local",
        source_filename="workflow.json",
        editor_json=None,
        api_json=export_workflow(api, WorkflowFormat.API).decode(),
        editor_checksum=None,
        api_checksum=api.checksum,
        node_inventory_json="{}",
        dependency_inventory_json="{}",
        actor_user_id="admin",
    )
    before_events = store.admin_audit_events()

    with pytest.raises(ValueError, match="WORKFLOW_DUPLICATE_REVISION"):
        store.add_comfy_workflow_revision(
            "cw-paired",
            expected_revision=1,
            source_filename="workflow.json",
            editor_json=export_workflow(editor, WorkflowFormat.EDITOR).decode(),
            api_json=export_workflow(api, WorkflowFormat.API).decode(),
            editor_checksum=editor.checksum,
            api_checksum=api.checksum,
            node_inventory_json="{}",
            dependency_inventory_json="{}",
            actor_user_id="admin",
        )

    assert store.list_comfy_workflows()[0]["revision"] == 1
    assert store.admin_audit_events() == before_events


def test_store_rejects_no_format_for_template_creation_and_revision_append(tmp_path: Path) -> None:
    """A revision with no exportable format is corrupt durable workflow history."""
    store = CanvasStore(tmp_path)
    empty = {
        "source_filename": "workflow.json",
        "editor_json": None,
        "api_json": None,
        "editor_checksum": None,
        "api_checksum": None,
        "node_inventory_json": "{}",
        "dependency_inventory_json": "{}",
        "actor_user_id": "admin",
    }

    with pytest.raises(ValueError, match="WORKFLOW_FORMAT_REQUIRED"):
        store.create_comfy_workflow(
            workflow_id="cw-empty",
            display_name="Empty",
            description="",
            service_id="comfy-local",
            **empty,
        )

    api = parse_workflow_json(API_BYTES)
    store.create_comfy_workflow(
        workflow_id="cw-valid",
        display_name="Valid",
        description="",
        service_id="comfy-local",
        source_filename="workflow.json",
        editor_json=None,
        api_json=export_workflow(api, WorkflowFormat.API).decode(),
        editor_checksum=None,
        api_checksum=api.checksum,
        node_inventory_json="{}",
        dependency_inventory_json="{}",
        actor_user_id="admin",
    )
    before_events = store.admin_audit_events()

    with pytest.raises(ValueError, match="WORKFLOW_FORMAT_REQUIRED"):
        store.add_comfy_workflow_revision("cw-valid", expected_revision=1, **empty)

    assert store.list_comfy_workflows()[0]["revision"] == 1
    assert store.admin_audit_events() == before_events


def test_store_requires_a_checksum_for_each_supplied_format(tmp_path: Path) -> None:
    """An unchecksummed document cannot be treated as an immutable revision."""
    store = CanvasStore(tmp_path)
    editor = parse_workflow_json(FIXTURE.read_bytes())

    with pytest.raises(ValueError, match="WORKFLOW_FORMAT_CHECKSUM_REQUIRED"):
        store.create_comfy_workflow(
            workflow_id="cw-no-checksum",
            display_name="No checksum",
            description="",
            service_id="comfy-local",
            source_filename="workflow.json",
            editor_json=export_workflow(editor, WorkflowFormat.EDITOR).decode(),
            api_json=None,
            editor_checksum=None,
            api_checksum=None,
            node_inventory_json="{}",
            dependency_inventory_json="{}",
            actor_user_id="admin",
        )

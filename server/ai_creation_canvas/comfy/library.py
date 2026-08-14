"""Durable, administrator-controlled ComfyUI workflow template library."""

from __future__ import annotations

from dataclasses import dataclass
import json
import secrets

from ai_creation_canvas.comfy.models import ParsedWorkflow, WorkflowFormat, WorkflowValidationError
from ai_creation_canvas.comfy.workflow_json import export_workflow, parse_workflow_json
from ai_creation_canvas.storage.sqlite import CanvasStore


_CORE_NODE_TYPES = frozenset({"LoadImage", "SaveImage", "LoadImageMask", "LoadLatent", "SaveImageWebsocket"})


@dataclass(frozen=True, slots=True)
class ComfyWorkflowTemplate:
    workflow_id: str
    display_name: str
    description: str
    service_id: str
    enabled: bool
    archived_at: str | None
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ComfyWorkflowProjection:
    workflow_id: str
    display_name: str
    description: str
    service_id: str
    enabled: bool
    revision: int


class ComfyWorkflowLibrary:
    """Owns lifecycle validation while the store owns atomic durable state."""

    def __init__(self, store: CanvasStore) -> None:
        self._store = store

    def create_template(
        self, display_name: str, service_id: str, parsed: ParsedWorkflow, *, actor_user_id: str
    ) -> ComfyWorkflowTemplate:
        self._require_text(display_name, "display name")
        self._require_text(service_id, "service id")
        self._require_text(actor_user_id, "actor user id")
        values = self._revision_values(parsed)
        record = self._store.create_comfy_workflow(
            workflow_id=f"cw-{secrets.token_urlsafe(24)}",
            display_name=display_name,
            description="",
            service_id=service_id,
            actor_user_id=actor_user_id,
            **values,
        )
        return self._template(record)

    def add_revision(
        self,
        workflow_id: str,
        parsed: ParsedWorkflow,
        *,
        expected_revision: int,
        actor_user_id: str,
    ) -> ComfyWorkflowTemplate:
        self._require_revision(expected_revision)
        self._require_text(actor_user_id, "actor user id")
        try:
            record = self._store.add_comfy_workflow_revision(
                workflow_id,
                expected_revision=expected_revision,
                actor_user_id=actor_user_id,
                **self._revision_values(parsed),
            )
        except ValueError as error:
            if str(error) == "WORKFLOW_DUPLICATE_REVISION":
                raise WorkflowValidationError("WORKFLOW_DUPLICATE_REVISION") from error
            raise WorkflowValidationError("WORKFLOW_REVISION_CONFLICT") from error
        return self._template(record)

    def set_lifecycle(
        self,
        workflow_id: str,
        *,
        expected_revision: int,
        actor_user_id: str,
        enabled: bool | None = None,
        archived: bool | None = None,
    ) -> ComfyWorkflowTemplate:
        self._require_revision(expected_revision)
        self._require_text(actor_user_id, "actor user id")
        try:
            record = self._store.set_comfy_workflow_lifecycle(
                workflow_id,
                expected_revision=expected_revision,
                enabled=enabled,
                archived=archived,
                actor_user_id=actor_user_id,
            )
        except ValueError as error:
            raise WorkflowValidationError("WORKFLOW_REVISION_CONFLICT") from error
        return self._template(record)

    def replace_assignments(
        self, user_id: str, workflow_ids: tuple[str, ...], *, actor_user_id: str
    ) -> tuple[str, ...]:
        self._require_text(user_id, "user id")
        self._require_text(actor_user_id, "actor user id")
        return self._store.replace_comfy_workflow_assignments(
            user_id, workflow_ids, actor_user_id=actor_user_id
        )

    def admin_list(self) -> tuple[ComfyWorkflowTemplate, ...]:
        return tuple(self._template(record) for record in self._store.list_comfy_workflows())

    def assigned_list(self, user_id: str) -> tuple[ComfyWorkflowProjection, ...]:
        self._require_text(user_id, "user id")
        return tuple(self._projection(record) for record in self._store.assigned_comfy_workflows(user_id))

    def export_revision(self, workflow_id: str, revision: int, format: WorkflowFormat) -> bytes:
        self._require_revision(revision)
        record = self._store.comfy_workflow_revision(workflow_id, revision)
        if record is None:
            raise KeyError((workflow_id, revision))
        try:
            selected = WorkflowFormat(format)
        except ValueError as error:
            raise WorkflowValidationError("WORKFLOW_FORMAT_UNAVAILABLE") from error
        raw = record[f"{selected.value}_json"]
        if not isinstance(raw, str):
            raise WorkflowValidationError("WORKFLOW_FORMAT_UNAVAILABLE")
        return export_workflow(parse_workflow_json(raw.encode("utf-8")), selected)

    @staticmethod
    def _revision_values(parsed: ParsedWorkflow) -> dict[str, str | None]:
        if not isinstance(parsed, ParsedWorkflow) or len(parsed.formats) != 1:
            raise WorkflowValidationError("WORKFLOW_FORMAT_UNSUPPORTED")
        format = next(iter(parsed.formats))
        encoded = export_workflow(parsed, format).decode("utf-8")
        node_inventory = {
            "node_count": parsed.node_count,
            "link_count": parsed.link_count,
            "nodes": [
                {"id": node.id, "type": node.type, "title": node.title}
                for node in parsed.preview.nodes
            ],
        }
        dependency_inventory = {
            "node_types": [
                {"type": node_type, "is_core": node_type in _CORE_NODE_TYPES}
                for node_type in sorted(parsed.node_types)
            ]
        }
        return {
            "source_filename": "workflow.json",
            "editor_json": encoded if format is WorkflowFormat.EDITOR else None,
            "api_json": encoded if format is WorkflowFormat.API else None,
            "editor_checksum": parsed.checksum if format is WorkflowFormat.EDITOR else None,
            "api_checksum": parsed.checksum if format is WorkflowFormat.API else None,
            "node_inventory_json": json.dumps(node_inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "dependency_inventory_json": json.dumps(
                dependency_inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
        }

    @staticmethod
    def _template(record: dict[str, object]) -> ComfyWorkflowTemplate:
        return ComfyWorkflowTemplate(
            workflow_id=str(record["workflow_id"]),
            display_name=str(record["display_name"]),
            description=str(record["description"]),
            service_id=str(record["service_id"]),
            enabled=bool(record["enabled"]),
            archived_at=str(record["archived_at"]) if record["archived_at"] is not None else None,
            revision=int(record["revision"]),
            created_at=str(record["created_at"]),
            updated_at=str(record["updated_at"]),
        )

    @staticmethod
    def _projection(record: dict[str, object]) -> ComfyWorkflowProjection:
        return ComfyWorkflowProjection(
            workflow_id=str(record["workflow_id"]),
            display_name=str(record["display_name"]),
            description=str(record["description"]),
            service_id=str(record["service_id"]),
            enabled=bool(record["enabled"]),
            revision=int(record["revision"]),
        )

    @staticmethod
    def _require_text(value: str, field: str) -> None:
        if not isinstance(value, str) or not value or len(value) > 128:
            raise ValueError(f"workflow {field} is invalid")

    @staticmethod
    def _require_revision(revision: int) -> None:
        if type(revision) is not int or revision < 1:
            raise WorkflowValidationError("WORKFLOW_REVISION_CONFLICT")

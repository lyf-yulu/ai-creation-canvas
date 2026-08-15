"""RBAC-protected, inert ComfyUI workflow-library endpoints."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator
from python_multipart.exceptions import MultipartParseError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from ai_creation_canvas.api._common import context_for, problem
from ai_creation_canvas.comfy import WorkflowFormat, WorkflowValidationError, parse_workflow_json
from ai_creation_canvas.comfy.library import ComfyWorkflowProjection, ComfyWorkflowTemplate
from ai_creation_canvas.domain.models import PortalRole


router = APIRouter(prefix="/api/v1")
_WORKFLOW_JSON_MAX_BYTES = 4 * 1024 * 1024
_MULTIPART_OVERHEAD_MAX_BYTES = 64 * 1024
_MULTIPART_HEADER_MAX_BYTES = 32 * 1024
_REVISION_TEXT = re.compile(r"[1-9][0-9]{0,9}\Z")


class LifecycleRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: StrictInt = Field(ge=1)


class WorkflowAssignments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workflow_ids: list[str] = Field(max_length=128)

    @field_validator("workflow_ids")
    @classmethod
    def valid_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(not value or len(value) > 128 for value in values):
            raise ValueError("workflow IDs are invalid")
        return values


@dataclass(frozen=True, slots=True)
class _WorkflowForm:
    file: UploadFile
    fields: dict[str, str]


class _BoundedWorkflowStream:
    def __init__(self, request: Request) -> None:
        self._source = request.stream()
        self._consumed = 0

    async def __aiter__(self):
        async for chunk in self._source:
            self._consumed += len(chunk)
            if self._consumed > _WORKFLOW_JSON_MAX_BYTES + _MULTIPART_OVERHEAD_MAX_BYTES:
                raise MultiPartException("workflow upload is too large")
            yield chunk


class _WorkflowMultipartParser(MultiPartParser):
    def __init__(self, request: Request, *, expected_fields: int) -> None:
        super().__init__(
            request.headers,
            _BoundedWorkflowStream(request),
            max_files=1,
            max_fields=expected_fields,
            max_part_size=1024,
        )
        self.file_bytes = 0
        self.header_bytes = 0

    def on_part_begin(self) -> None:
        self.header_bytes = 0
        super().on_part_begin()

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._count_header(end - start)
        super().on_header_field(data, start, end)

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._count_header(end - start)
        super().on_header_value(data, start, end)

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            self.file_bytes += end - start
            if self.file_bytes > _WORKFLOW_JSON_MAX_BYTES:
                raise MultiPartException("workflow upload is too large")
        super().on_part_data(data, start, end)

    def _count_header(self, count: int) -> None:
        self.header_bytes += count
        if self.header_bytes > _MULTIPART_HEADER_MAX_BYTES:
            raise MultiPartException("workflow upload headers are too large")

    async def parse(self):
        try:
            return await super().parse()
        except BaseException:
            for temporary in self._files_to_close_on_error:
                temporary.close()
            raise


def _require_admin(request: Request) -> str:
    context = context_for(request)
    if context.user.role is not PortalRole.ADMIN:
        raise problem(request, "API_NOT_FOUND", "The requested API resource was not found.", status=404)
    return context.user.user_id


async def _single_workflow_form(request: Request, *, keys: frozenset[str]) -> _WorkflowForm:
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data;"):
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.")
    try:
        form = await _WorkflowMultipartParser(request, expected_fields=len(keys) - 1).parse()
    except (MultiPartException, MultipartParseError, ValueError):
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.") from None
    items = list(form.multi_items())
    if len(items) != len(keys) or {key for key, _ in items} != keys:
        await _close_uploads(items)
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.")
    file = form.get("file")
    if not isinstance(file, UploadFile) or file.content_type != "application/json":
        await _close_uploads(items)
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.")
    fields = {key: value for key, value in items if key != "file" and isinstance(value, str)}
    if len(fields) != len(keys) - 1:
        await _close_uploads(items)
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.")
    return _WorkflowForm(file=file, fields=fields)


async def _bounded_upload_bytes(upload: UploadFile) -> bytes:
    try:
        await upload.seek(0)
        data = await upload.read(_WORKFLOW_JSON_MAX_BYTES + 1)
    finally:
        await upload.close()
    if len(data) > _WORKFLOW_JSON_MAX_BYTES:
        raise WorkflowValidationError("WORKFLOW_SIZE_EXCEEDED")
    return data


async def _close_uploads(items: list[tuple[str, object]]) -> None:
    for _, value in items:
        if isinstance(value, UploadFile):
            await value.close()


def _revision_from_form(request: Request, text: str | None) -> int:
    if not isinstance(text, str) or _REVISION_TEXT.fullmatch(text) is None:
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.")
    return int(text)


def _workflow_error(request: Request, error: Exception) -> None:
    if isinstance(error, WorkflowValidationError):
        raise problem(request, error.code, "The workflow request was rejected.") from None
    raise problem(request, "REQUEST_REJECTED", "The request was rejected.") from None


def _admin_template(request: Request, workflow_id: str) -> ComfyWorkflowTemplate:
    for item in request.app.state.comfy_workflow_library.admin_list():
        if item.workflow_id == workflow_id:
            return item
    raise problem(request, "WORKFLOW_NOT_FOUND", "The requested workflow was not found.", status=404)


def _assigned_template(request: Request, workflow_id: str) -> ComfyWorkflowProjection:
    user_id = context_for(request).user.user_id
    for item in request.app.state.comfy_workflow_library.assigned_list(user_id):
        if item.workflow_id == workflow_id:
            return item
    # Deliberately perform access filtering before touching revision data.
    raise problem(request, "WORKFLOW_NOT_FOUND", "The requested workflow was not found.", status=404)


def _template_projection(item: ComfyWorkflowTemplate | ComfyWorkflowProjection) -> dict[str, object]:
    archived_at = item.archived_at if isinstance(item, ComfyWorkflowTemplate) else None
    return {
        "workflow_id": item.workflow_id,
        "display_name": item.display_name,
        "description": item.description,
        "service_id": item.service_id,
        "lifecycle": {"enabled": item.enabled, "archived": archived_at is not None},
        "revision": item.revision,
        "execution_available": False,
    }


def _revision_projection(request: Request, workflow_id: str, revision: int) -> dict[str, object]:
    library = request.app.state.comfy_workflow_library
    record = request.app.state.canvas_store.comfy_workflow_revision(workflow_id, revision)
    if record is None:
        raise problem(request, "WORKFLOW_NOT_FOUND", "The requested workflow was not found.", status=404)
    formats = [name for name in ("editor", "api") if record[f"{name}_json"] is not None]
    try:
        selected = WorkflowFormat(formats[0])
        parsed = parse_workflow_json(library.export_revision(workflow_id, revision, selected))
        dependencies = json.loads(str(record["dependency_inventory_json"]))
    except (WorkflowValidationError, ValueError, TypeError, json.JSONDecodeError):
        raise problem(request, "WORKFLOW_UNAVAILABLE", "The requested workflow is unavailable.", status=404) from None
    return {
        "workflow_id": workflow_id,
        "revision": revision,
        "formats": formats,
        "preview": {
            "nodes": [
                {"id": node.id, "type": node.type, "title": node.title, "position": list(node.position) if node.position else None}
                for node in parsed.preview.nodes
            ],
            "edges": [{"source_id": edge.source_id, "target_id": edge.target_id} for edge in parsed.preview.edges],
            "has_editor_layout": parsed.preview.has_editor_layout,
        },
        "dependencies": dependencies,
        "execution_available": False,
        "execution_unavailable_reason": "EXECUTION_NOT_IMPLEMENTED",
    }


def _latest_document_revision(request: Request, item: ComfyWorkflowTemplate | ComfyWorkflowProjection) -> int:
    """Lifecycle changes advance the optimistic version without rewriting a document revision."""
    for revision in range(item.revision, 0, -1):
        if request.app.state.canvas_store.comfy_workflow_revision(item.workflow_id, revision) is not None:
            return revision
    raise problem(request, "WORKFLOW_UNAVAILABLE", "The requested workflow is unavailable.", status=404)


def _export(request: Request, workflow_id: str, revision: int, format: WorkflowFormat) -> Response:
    try:
        content = request.app.state.comfy_workflow_library.export_revision(workflow_id, revision, format)
    except KeyError:
        raise problem(request, "WORKFLOW_NOT_FOUND", "The requested workflow was not found.", status=404) from None
    except WorkflowValidationError as error:
        raise problem(request, error.code, "The requested workflow format is unavailable.") from None
    filename = f"comfy-workflow-{workflow_id}-r{revision}-{format.value}.json"
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/admin/comfy-workflows/import", status_code=201)
async def import_workflow(request: Request) -> dict[str, object]:
    actor_user_id = _require_admin(request)
    form = await _single_workflow_form(request, keys=frozenset({"file", "display_name", "service_id"}))
    try:
        parsed = parse_workflow_json(await _bounded_upload_bytes(form.file))
        item = request.app.state.comfy_workflow_library.create_template(
            form.fields["display_name"], form.fields["service_id"], parsed, actor_user_id=actor_user_id
        )
    except (ValueError, WorkflowValidationError) as error:
        _workflow_error(request, error)
    return _template_projection(item)


@router.get("/admin/comfy-workflows")
async def admin_list_workflows(request: Request) -> dict[str, object]:
    _require_admin(request)
    return {"workflows": [_template_projection(item) for item in request.app.state.comfy_workflow_library.admin_list()]}


@router.get("/admin/comfy-workflows/{workflow_id}")
async def admin_get_workflow(workflow_id: str, request: Request) -> dict[str, object]:
    _require_admin(request)
    item = _admin_template(request, workflow_id)
    return {**_template_projection(item), "current_revision": _revision_projection(request, workflow_id, _latest_document_revision(request, item))}


@router.get("/admin/comfy-workflows/{workflow_id}/revisions/{revision}/preview")
async def admin_preview_workflow(workflow_id: str, revision: int, request: Request) -> dict[str, object]:
    _require_admin(request)
    _admin_template(request, workflow_id)
    return _revision_projection(request, workflow_id, revision)


@router.get("/admin/comfy-workflows/{workflow_id}/revisions/{revision}/export")
async def admin_export_workflow(workflow_id: str, revision: int, request: Request, format: WorkflowFormat = Query(...)) -> Response:
    _require_admin(request)
    _admin_template(request, workflow_id)
    return _export(request, workflow_id, revision, format)


@router.post("/admin/comfy-workflows/{workflow_id}/revisions", status_code=201)
async def add_workflow_revision(workflow_id: str, request: Request) -> dict[str, object]:
    actor_user_id = _require_admin(request)
    current = _admin_template(request, workflow_id)
    if current.enabled:
        raise problem(request, "WORKFLOW_REVISION_CONFLICT", "The workflow must be disabled before revision changes.")
    form = await _single_workflow_form(request, keys=frozenset({"file", "revision"}))
    expected_revision = _revision_from_form(request, form.fields.get("revision"))
    try:
        parsed = parse_workflow_json(await _bounded_upload_bytes(form.file))
        item = request.app.state.comfy_workflow_library.add_revision(
            workflow_id, parsed, expected_revision=expected_revision, actor_user_id=actor_user_id
        )
    except (ValueError, WorkflowValidationError) as error:
        _workflow_error(request, error)
    return _template_projection(item)


async def _transition(workflow_id: str, request: Request, *, enabled: bool | None = None, archived: bool | None = None) -> dict[str, object]:
    actor_user_id = _require_admin(request)
    current = _admin_template(request, workflow_id)
    if enabled:
        _latest_document_revision(request, current)
        configured_service_ids = {
            str(service.service_id) for service in request.app.state.comfy_workflow_services
            if isinstance(getattr(service, "service_id", None), str)
        }
        if current.service_id not in configured_service_ids:
            raise problem(request, "WORKFLOW_SERVICE_UNAVAILABLE", "The workflow service is unavailable.")
    try:
        body = LifecycleRevision.model_validate(await request.json())
        item = request.app.state.comfy_workflow_library.set_lifecycle(
            workflow_id, expected_revision=body.revision, actor_user_id=actor_user_id, enabled=enabled, archived=archived
        )
    except (ValidationError, ValueError, WorkflowValidationError) as error:
        _workflow_error(request, error)
    return _template_projection(item)


@router.post("/admin/comfy-workflows/{workflow_id}/enable")
async def enable_workflow(workflow_id: str, request: Request) -> dict[str, object]:
    return await _transition(workflow_id, request, enabled=True)


@router.post("/admin/comfy-workflows/{workflow_id}/disable")
async def disable_workflow(workflow_id: str, request: Request) -> dict[str, object]:
    return await _transition(workflow_id, request, enabled=False)


@router.post("/admin/comfy-workflows/{workflow_id}/archive")
async def archive_workflow(workflow_id: str, request: Request) -> dict[str, object]:
    return await _transition(workflow_id, request, archived=True)


@router.post("/admin/comfy-workflows/{workflow_id}/restore")
async def restore_workflow(workflow_id: str, request: Request) -> dict[str, object]:
    return await _transition(workflow_id, request, archived=False)


@router.put("/admin/users/{user_id}/comfy-workflows")
async def replace_workflow_assignments(user_id: str, request: Request) -> dict[str, object]:
    actor_user_id = _require_admin(request)
    if request.app.state.canvas_store.user_by_id(user_id) is None:
        raise problem(request, "USER_NOT_FOUND", "The requested user was not found.", status=404)
    try:
        body = WorkflowAssignments.model_validate(await request.json())
        workflow_ids = request.app.state.comfy_workflow_library.replace_assignments(
            user_id, tuple(body.workflow_ids), actor_user_id=actor_user_id
        )
    except (ValidationError, ValueError, KeyError):
        raise problem(request, "WORKFLOW_UNAVAILABLE", "The selected workflow is unavailable.") from None
    return {"user_id": user_id, "workflow_ids": list(workflow_ids)}


@router.get("/comfy-workflows")
async def list_assigned_workflows(request: Request) -> dict[str, object]:
    context_for(request)
    return {"workflows": [_template_projection(item) for item in request.app.state.comfy_workflow_library.assigned_list(context_for(request).user.user_id)]}


@router.get("/comfy-workflows/{workflow_id}")
async def get_assigned_workflow(workflow_id: str, request: Request) -> dict[str, object]:
    item = _assigned_template(request, workflow_id)
    return {**_template_projection(item), "current_revision": _revision_projection(request, workflow_id, _latest_document_revision(request, item))}


@router.get("/comfy-workflows/{workflow_id}/revisions/{revision}/preview")
async def preview_assigned_workflow(workflow_id: str, revision: int, request: Request) -> dict[str, object]:
    item = _assigned_template(request, workflow_id)
    if revision != _latest_document_revision(request, item):
        raise problem(request, "WORKFLOW_NOT_FOUND", "The requested workflow was not found.", status=404)
    return _revision_projection(request, workflow_id, revision)


@router.get("/comfy-workflows/{workflow_id}/revisions/{revision}/export")
async def export_assigned_workflow(workflow_id: str, revision: int, request: Request, format: WorkflowFormat = Query(...)) -> Response:
    item = _assigned_template(request, workflow_id)
    if revision != _latest_document_revision(request, item):
        raise problem(request, "WORKFLOW_NOT_FOUND", "The requested workflow was not found.", status=404)
    if format is WorkflowFormat.API:
        raise problem(request, "WORKFLOW_FORMAT_UNAVAILABLE", "The requested workflow format is unavailable.")
    return _export(request, workflow_id, revision, format)

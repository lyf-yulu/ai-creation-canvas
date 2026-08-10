"""Owner-isolated, bounded canvas project persistence."""

from __future__ import annotations

import json
import math
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ai_creation_canvas.api._common import context_for, problem


router = APIRouter(prefix="/api/v1/projects")
_MAX_DOCUMENT_BYTES = 1024 * 1024
_MAX_DEPTH = 32


class Viewport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    x: float | int
    y: float | int
    k: float | int


class ProjectDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    createdAt: str = Field(min_length=1, max_length=64)
    updatedAt: str = Field(min_length=1, max_length=64)
    nodes: list[dict[str, Any]] = Field(max_length=1000)
    connections: list[dict[str, Any]] = Field(max_length=2000)
    chatSessions: list[dict[str, Any]] = Field(max_length=1000)
    activeChatId: str | None = Field(default=None, max_length=128)
    backgroundMode: Literal["dots", "lines", "blank"]
    showImageInfo: bool
    viewport: Viewport


class ProjectUpdate(ProjectDocument):
    expected_version: int = Field(ge=1)


def _bounded_document(model: ProjectDocument) -> tuple[dict[str, object], str]:
    document = model.model_dump(exclude={"expected_version"})
    stack: list[tuple[object, int]] = [(document, 1)]
    while stack:
        value, depth = stack.pop()
        if depth > _MAX_DEPTH:
            raise ValueError("project document is too deep")
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError("project document contains a non-finite number")
    encoded = json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_DOCUMENT_BYTES:
        raise ValueError("project document is too large")
    return document, encoded


def _envelope(row: dict[str, object]) -> dict[str, object]:
    return {"project": json.loads(str(row["document_json"])), "version": int(row["version"])}


@router.get("")
async def list_projects(request: Request) -> dict[str, object]:
    user = context_for(request).user
    rows = request.app.state.canvas_store.list_projects_for_owner(user.user_id)
    return {"projects": [_envelope(row) for row in rows]}


@router.post("")
async def create_project(body: ProjectDocument, request: Request) -> JSONResponse:
    user = context_for(request).user
    try:
        _, encoded = _bounded_document(body)
    except (TypeError, ValueError):
        raise problem(request, "REQUEST_REJECTED", "The project document was rejected.", status=400) from None
    row, created, conflict = request.app.state.canvas_store.create_project(
        user_id=user.user_id, project_id=body.id, title=body.title, document_json=encoded,
    )
    if conflict:
        raise problem(request, "PROJECT_CONFLICT", "The project has changed.", status=409)
    return JSONResponse(status_code=201 if created else 200, content=_envelope(row))


@router.get("/{project_id}")
async def get_project(project_id: str, request: Request) -> dict[str, object]:
    user = context_for(request).user
    row = request.app.state.canvas_store.project_for_owner(project_id, user.user_id)
    if row is None:
        raise problem(request, "PROJECT_NOT_FOUND", "The requested project was not found.", status=404)
    return _envelope(row)


@router.put("/{project_id}")
async def update_project(project_id: str, body: ProjectUpdate, request: Request) -> dict[str, object]:
    user = context_for(request).user
    if body.id != project_id:
        raise problem(request, "REQUEST_REJECTED", "The project document was rejected.", status=400)
    try:
        _, encoded = _bounded_document(body)
    except (TypeError, ValueError):
        raise problem(request, "REQUEST_REJECTED", "The project document was rejected.", status=400) from None
    store = request.app.state.canvas_store
    if store.project_for_owner(project_id, user.user_id) is None:
        raise problem(request, "PROJECT_NOT_FOUND", "The requested project was not found.", status=404)
    row = store.update_project(
        user_id=user.user_id, project_id=project_id, title=body.title,
        document_json=encoded, expected_version=body.expected_version,
    )
    if row is None:
        raise problem(request, "PROJECT_CONFLICT", "The project has changed.", status=409)
    return _envelope(row)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, request: Request) -> Response:
    user = context_for(request).user
    if not request.app.state.canvas_store.delete_project(user_id=user.user_id, project_id=project_id):
        raise problem(request, "PROJECT_NOT_FOUND", "The requested project was not found.", status=404)
    return Response(status_code=204)

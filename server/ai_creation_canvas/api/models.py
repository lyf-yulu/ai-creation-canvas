"""Authenticated, capability-driven model catalog endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder

from ai_creation_canvas.adapters.portal.identity import AuthRequired
from ai_creation_canvas.catalog import ModelCatalogPort
from ai_creation_canvas.domain.models import PortalUser, RequestContext


router = APIRouter(prefix="/api/v1")


@router.get("/models")
async def get_models(request: Request) -> dict[str, object]:
    user = getattr(request.state, "portal_user", None)
    request_id = getattr(request.state, "request_id", "identity")
    if not isinstance(user, PortalUser) or not request.headers.get("cookie"):
        raise AuthRequired(request_id)
    catalog = getattr(request.app.state, "model_catalog", None)
    if not isinstance(catalog, ModelCatalogPort):
        raise RuntimeError("model catalog is not configured")
    context = RequestContext(user=user, request_id=request_id, trace_id=request_id)
    result = await catalog.list_models(context, cookie_header=request.headers.get("cookie"))
    return {"models": jsonable_encoder(result.models), "diagnostics": result.diagnostics}

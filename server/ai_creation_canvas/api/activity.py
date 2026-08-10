"""Owned asset and job metadata for the product activity pages."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ai_creation_canvas.api._common import context_for


router = APIRouter(prefix="/api/v1/activity")


@router.get("/assets")
async def list_assets(request: Request) -> dict[str, object]:
    context = context_for(request)
    return {"assets": request.app.state.canvas_store.list_assets_for_owner(context.user.user_id)}


@router.get("/jobs")
async def list_jobs(request: Request) -> dict[str, object]:
    context = context_for(request)
    return {"jobs": request.app.state.canvas_store.list_jobs_for_owner(context.user.user_id)}

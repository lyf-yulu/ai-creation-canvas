"""Current owner's charged generation usage."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ai_creation_canvas.api._common import context_for


router = APIRouter(prefix="/api/v1/usage")


@router.get("")
async def usage(request: Request) -> dict[str, object]:
    return request.app.state.canvas_store.usage_for_owner(context_for(request).user.user_id)

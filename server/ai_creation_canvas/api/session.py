"""Verified Portal session endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ai_creation_canvas.adapters.portal.identity import AuthRequired
from ai_creation_canvas.domain.models import PortalUser


router = APIRouter(prefix="/api/v1")


@router.get("/session")
async def get_session(request: Request) -> dict[str, str]:
    user = getattr(request.state, "portal_user", None)
    if not isinstance(user, PortalUser):
        raise AuthRequired(getattr(request.state, "request_id", "identity"))
    return {"user_id": user.user_id, "username": user.username, "role": user.role.value}

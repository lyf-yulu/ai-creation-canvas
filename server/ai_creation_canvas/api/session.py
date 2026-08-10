"""Verified Portal session endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ai_creation_canvas.adapters.portal.identity import AuthRequired
from ai_creation_canvas.auth.local import LocalAuthService
from ai_creation_canvas.domain.models import PortalUser


router = APIRouter(prefix="/api/v1")


@router.get("/session")
async def get_session(request: Request) -> dict[str, object]:
    user = getattr(request.state, "portal_user", None)
    if not isinstance(user, PortalUser):
        raise AuthRequired(getattr(request.state, "request_id", "identity"))
    payload: dict[str, object] = {"user_id": user.user_id, "username": user.username, "role": user.role.value}
    auth = getattr(request.app.state, "local_auth", None)
    if isinstance(auth, LocalAuthService):
        settings = request.app.state.settings
        details = auth.session_details(request.cookies.get(settings.session_cookie_name, ""))
        if details is None:
            raise AuthRequired(getattr(request.state, "request_id", "identity"))
        payload["must_change_password"] = bool(details["must_change_password"])
        payload["csrf_token"] = str(details["csrf_token"])
    return payload

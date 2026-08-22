"""Verified Portal session endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ai_creation_canvas.adapters.portal.identity import AuthRequired
from ai_creation_canvas.api._common import problem
from ai_creation_canvas.auth.local import LocalAuthService
from ai_creation_canvas.domain.models import PortalUser
from ai_creation_canvas.storage.sqlite import CanvasStore
from ai_creation_canvas.user_skin import DEFAULT_SKIN, SKIN_PRESETS, SKIN_TOKENS, encode_skin, validate_skin


router = APIRouter(prefix="/api/v1")


def _require_local_user(request: Request) -> tuple[PortalUser, CanvasStore]:
    user = getattr(request.state, "portal_user", None)
    if not isinstance(user, PortalUser):
        raise AuthRequired(getattr(request.state, "request_id", "identity"))
    store = request.app.state.canvas_store
    if not isinstance(store, CanvasStore):
        raise AuthRequired(getattr(request.state, "request_id", "identity"))
    return user, store


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
    store = request.app.state.canvas_store
    if isinstance(store, CanvasStore):
        payload["skin"] = store.user_skin(user.user_id) or dict(DEFAULT_SKIN)
        payload["skin_presets"] = {name: dict(colors) for name, colors in SKIN_PRESETS.items()}
    return payload


class SkinUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1, le=1)
    colors: dict[str, str] = Field(min_length=1, max_length=64)


@router.put("/session/skin")
async def update_session_skin(body: SkinUpdate, request: Request) -> dict[str, object]:
    user, store = _require_local_user(request)
    try:
        merged = validate_skin({"version": body.version, "colors": {str(name): str(value) for name, value in body.colors.items()}})
        if set(merged) != set(SKIN_TOKENS):
            raise ValueError("skin colors are incomplete")
    except ValueError as error:
        raise problem(request, "SKIN_INVALID", f"皮肤配置无效：{error}", status=400) from None
    store.set_user_skin(user.user_id, encode_skin(merged))
    return {"skin": merged}

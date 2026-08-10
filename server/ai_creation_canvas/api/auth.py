"""Local Cookie authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ai_creation_canvas.api._common import problem
from ai_creation_canvas.auth.local import IssuedSession, LocalAuthService


router = APIRouter(prefix="/api/v1/auth")


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=128)


class ChangePasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    current_password: str = Field(min_length=12, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


def _auth(request: Request) -> LocalAuthService:
    auth = getattr(request.app.state, "local_auth", None)
    if not isinstance(auth, LocalAuthService):
        raise problem(request, "API_NOT_FOUND", "The requested API resource was not found.", status=404)
    return auth


def _user_payload(issued: IssuedSession) -> dict[str, object]:
    return {
        "user_id": issued.user.user_id,
        "username": issued.user.username,
        "role": issued.user.role.value,
        "must_change_password": issued.must_change_password,
    }


def _set_cookie(request: Request, response: Response, issued: IssuedSession) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        settings.session_cookie_name,
        issued.session_token,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        path="/",
        max_age=settings.session_ttl_seconds,
    )


@router.post("/login")
async def login(body: LoginBody, request: Request) -> Response:
    try:
        issued = _auth(request).login(body.username, body.password)
    except ValueError:
        raise problem(request, "AUTH_REQUIRED", "Sign in is required.", status=401, phase="authentication") from None
    response = JSONResponse({"user": _user_payload(issued), "csrf_token": issued.csrf_token})
    _set_cookie(request, response, issued)
    return response


@router.post("/logout", status_code=204)
async def logout(request: Request) -> Response:
    settings = request.app.state.settings
    session_token = request.cookies.get(settings.session_cookie_name, "")
    _auth(request).logout(session_token)
    response = Response(status_code=204)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response


@router.post("/change-password")
async def change_password(body: ChangePasswordBody, request: Request) -> Response:
    settings = request.app.state.settings
    auth = _auth(request)
    old_token = request.cookies.get(settings.session_cookie_name, "")
    details = auth.session_details(old_token)
    if details is None:
        raise problem(request, "AUTH_REQUIRED", "Sign in is required.", status=401, phase="authentication")
    try:
        auth.change_initial_password(str(details["user_id"]), body.current_password, body.new_password)
        issued = auth.login(str(details["username_normalized"]), body.new_password)
    except ValueError:
        raise problem(request, "AUTH_REQUIRED", "Sign in is required.", status=401, phase="authentication") from None
    response = JSONResponse({"user": _user_payload(issued), "csrf_token": issued.csrf_token})
    _set_cookie(request, response, issued)
    return response

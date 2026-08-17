"""Local Cookie authentication endpoints."""

from __future__ import annotations

import sqlite3
import time
from typing import Callable

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


class RegisterBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=128)


class _RegisterRateLimiter:
    """Best-effort per-process, per-IP registration throttle for standalone local mode."""

    def __init__(self, *, limit: int = 10, window_seconds: float = 3600, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, list[float]] = {}

    def allow(self, client_ip: str) -> bool:
        now = self._clock()
        recent = [hit for hit in self._hits.get(client_ip, ()) if now - hit < self._window]
        if len(recent) >= self._limit:
            self._hits[client_ip] = recent
            return False
        recent.append(now)
        self._hits[client_ip] = recent
        return True

    def reset(self) -> None:
        self._hits.clear()


_register_rate_limiter = _RegisterRateLimiter()


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


@router.post("/register", status_code=201)
async def register(body: RegisterBody, request: Request) -> dict[str, object]:
    auth = _auth(request)
    client_ip = request.client.host if request.client is not None else "unknown"
    if not _register_rate_limiter.allow(client_ip):
        raise problem(request, "RATE_LIMITED", "注册过于频繁，请稍后再试。", status=429, retryable=True)
    try:
        auth.register_user(body.username, body.display_name, body.password)
    except sqlite3.IntegrityError:
        raise problem(request, "USERNAME_TAKEN", "用户名已被占用。", status=409) from None
    except ValueError:
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.") from None
    return {"registered": True}


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

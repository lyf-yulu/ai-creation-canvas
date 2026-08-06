"""FastAPI application factory for the isolated Canvas service."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from ai_creation_canvas.adapters.portal.identity import AuthRequired, verify_portal_identity
from ai_creation_canvas.api.session import router as session_router
from ai_creation_canvas.config import Settings
from ai_creation_canvas.errors import ApiError, DomainError


_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'"


def _request_id(value: str | None) -> str:
    return value if value and _REQUEST_ID.fullmatch(value) else uuid.uuid4().hex


def _error_response(error: DomainError) -> JSONResponse:
    return JSONResponse(status_code=401 if error.api_error.code == "AUTH_REQUIRED" else 400, content=error.api_error.to_dict())


def _safe_static_file(static_dir: Path, path: str) -> Path | None:
    candidate = (static_dir / path.lstrip("/")).resolve(strict=False)
    try:
        candidate.relative_to(static_dir)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def create_app(settings: Settings, *, static_dir: Path | str | None = None) -> FastAPI:
    """Create a service with signed API access and a deliberately narrow SPA fallback."""
    app = FastAPI()
    build_dir = Path(static_dir) if static_dir is not None else Path(__file__).parents[2] / "web" / "dist"
    build_dir = build_dir.resolve(strict=False)

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        request.state.request_id = _request_id(request.headers.get("x-request-id"))
        try:
            if request.url.path.startswith("/api/v1/"):
                request.state.portal_user = verify_portal_identity(
                    request.headers,
                    settings.portal_internal_token,
                    max_age_seconds=settings.signature_ttl_seconds,
                )
            response = await call_next(request)
        except DomainError as error:
            error.api_error.request_id  # preserve type-checker visibility; errors carry no secret.
            if error.api_error.request_id == "identity":
                error = AuthRequired(request.state.request_id)
            response = _error_response(error)
        except Exception:
            response = JSONResponse(
                status_code=500,
                content=ApiError(
                    code="INTERNAL_ERROR",
                    message="The request could not be completed.",
                    retryable=False,
                    request_id=request.state.request_id,
                    phase="request",
                ).to_dict(),
            )
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, error: DomainError) -> JSONResponse:
        return _error_response(error)

    app.include_router(session_router)

    @app.get("/{requested_path:path}", include_in_schema=False)
    async def static_or_spa(request: Request, requested_path: str) -> Response:
        if request.method != "GET":
            return Response(status_code=405)
        if requested_path:
            asset = _safe_static_file(build_dir, requested_path)
            if asset is not None:
                return FileResponse(asset)
            if Path(requested_path).suffix:
                return Response(status_code=404)
        accepts_html = "text/html" in request.headers.get("accept", "").lower()
        index = _safe_static_file(build_dir, "index.html")
        if accepts_html and index is not None:
            return FileResponse(index, media_type="text/html")
        return Response(status_code=404)

    return app

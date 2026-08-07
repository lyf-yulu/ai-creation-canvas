"""FastAPI application factory for the isolated Canvas service."""

from __future__ import annotations

import re
import stat
import uuid
from enum import StrEnum
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.adapters.portal.catalog import PortalJobsAdapter
from ai_creation_canvas.adapters.portal.client import PortalClient
from ai_creation_canvas.adapters.portal.identity import AuthRequired, verify_portal_identity
from ai_creation_canvas.api.models import router as models_router
from ai_creation_canvas.api.session import router as session_router
from ai_creation_canvas.api.assets import router as assets_router
from ai_creation_canvas.api.jobs import router as jobs_router
from ai_creation_canvas.api.results import router as results_router
from ai_creation_canvas.config import Settings, load_service_declarations
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.errors import ApiError, DomainError
from ai_creation_canvas.storage.sqlite import CanvasStore


_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'"


def _request_id(value: str | None) -> str:
    return value if value and _REQUEST_ID.fullmatch(value) else uuid.uuid4().hex


class StaticPathState(StrEnum):
    LEGIT_FILE = "legit_file"
    NOT_FOUND = "not_found"
    REJECTED = "rejected"


def _is_api_v1_path(path: str) -> bool:
    return path == "/api/v1" or path.startswith("/api/v1/")


def _error_response(error: DomainError, request_id: str) -> JSONResponse:
    public_error = ApiError(
        code=error.api_error.code,
        message=error.api_error.message,
        retryable=error.api_error.retryable,
        request_id=request_id,
        phase=error.api_error.phase,
    )
    return JSONResponse(status_code=getattr(error, "status_code", 401 if public_error.code == "AUTH_REQUIRED" else 400), content=public_error.to_dict())


def _static_path_state(static_dir: Path, path: str) -> tuple[StaticPathState, Path | None]:
    if not isinstance(path, str) or "\x00" in path or path.startswith(("/", "\\")) or "\\" in path:
        return StaticPathState.REJECTED, None
    try:
        root = static_dir.resolve(strict=False)
        components = path.split("/")
        if not components or any(component in {"", ".", ".."} for component in components):
            return StaticPathState.REJECTED, None
        candidate = root
        for component in components:
            candidate = candidate / component
            try:
                candidate_info = candidate.lstat()
            except FileNotFoundError:
                return StaticPathState.NOT_FOUND, None
            if stat.S_ISLNK(candidate_info.st_mode):
                return StaticPathState.REJECTED, None
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return StaticPathState.REJECTED, None
    try:
        resolved.relative_to(root)
    except ValueError:
        return StaticPathState.REJECTED, None
    try:
        if not stat.S_ISREG(resolved.stat().st_mode):
            return StaticPathState.REJECTED, None
    except (OSError, RuntimeError, ValueError):
        return StaticPathState.REJECTED, None
    return StaticPathState.LEGIT_FILE, resolved


def _safe_static_file(static_dir: Path, path: str) -> Path | None:
    """Compatibility helper; callers needing fallback must use the explicit state."""
    state, candidate = _static_path_state(static_dir, path)
    return candidate if state is StaticPathState.LEGIT_FILE else None


def create_app(settings: Settings, *, static_dir: Path | str | None = None, model_catalog: ModelCatalog | None = None, registry: AdapterRegistry | None = None, canvas_store: CanvasStore | None = None, portal_transport=None) -> FastAPI:
    """Create a service with signed API access and a deliberately narrow SPA fallback."""
    app = FastAPI()
    if registry is None:
        registry = AdapterRegistry()
    if model_catalog is None:
        if settings.services_config_path is not None:
            declarations = load_service_declarations(settings.services_config_path, settings.services_config_root)
            client = PortalClient(settings.portal_base_url, allowed_mounts=tuple(item.mount for item in declarations), verify=settings.portal_ca_file or True, allowed_methods=("GET", "POST", "HEAD"), allow_loopback_http=settings.portal_allow_loopback_http, max_concurrency=settings.portal_max_concurrency, transport=portal_transport)
            for declaration in declarations:
                registry.register_generation(PortalJobsAdapter(declaration, client))
        model_catalog = ModelCatalog(registry)
    app.state.model_catalog = model_catalog
    app.state.adapter_registry = registry
    app.state.canvas_store = canvas_store or CanvasStore(settings.data_dir)
    build_dir = Path(static_dir) if static_dir is not None else Path(__file__).parents[2] / "web" / "dist"
    build_dir = build_dir.resolve(strict=False)

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        request.state.request_id = _request_id(request.headers.get("x-request-id"))
        try:
            if _is_api_v1_path(request.url.path):
                request.state.portal_user = verify_portal_identity(
                    request.headers,
                    settings.portal_internal_token,
                    max_age_seconds=settings.signature_ttl_seconds,
                )
            response = await call_next(request)
        except DomainError as error:
            response = _error_response(error, request.state.request_id)
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
        return _error_response(error, _request_id(getattr(request.state, "request_id", None)))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, error: RequestValidationError) -> JSONResponse:
        del error
        request_id = _request_id(getattr(request.state, "request_id", None))
        return JSONResponse(status_code=400, content=ApiError("REQUEST_REJECTED", "The request was rejected.", False, request_id, "request").to_dict())

    app.include_router(session_router)
    app.include_router(models_router)
    app.include_router(assets_router)
    app.include_router(jobs_router)
    app.include_router(results_router)

    @app.api_route("/api/v1", methods=["GET", "HEAD", "OPTIONS"], include_in_schema=False)
    @app.api_route("/api/v1/", methods=["GET", "HEAD", "OPTIONS"], include_in_schema=False)
    async def api_namespace_not_found() -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    @app.get("/{requested_path:path}", include_in_schema=False)
    async def static_or_spa(request: Request, requested_path: str) -> Response:
        if request.method != "GET":
            return Response(status_code=405)
        if requested_path:
            state, asset = _static_path_state(build_dir, requested_path)
            if state is StaticPathState.LEGIT_FILE:
                assert asset is not None
                return FileResponse(asset)
            if state is StaticPathState.REJECTED or Path(requested_path).suffix:
                return Response(status_code=404)
        accepts_html = "text/html" in request.headers.get("accept", "").lower()
        index_state, index = _static_path_state(build_dir, "index.html")
        if accepts_html and index_state is StaticPathState.LEGIT_FILE:
            assert index is not None
            return FileResponse(index, media_type="text/html")
        return Response(status_code=404)

    return app

"""FastAPI application factory for the isolated Canvas service."""

from __future__ import annotations

import asyncio
import re
import stat
import uuid
from enum import StrEnum
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import FileResponse, JSONResponse, Response

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.adapters.portal.catalog import PortalJobsAdapter
from ai_creation_canvas.adapters.portal.portrait import PortalPortraitAdapter, PortraitDeclaration
from ai_creation_canvas.adapters.portal.client import PortalClient
from ai_creation_canvas.adapters.portal.identity import AuthRequired, verify_portal_identity
from ai_creation_canvas.adapters.demo import DemoGenerationAdapter
from ai_creation_canvas.adapters.ark import build_ark_adapters, _local_asset_loader
from ai_creation_canvas.adapters.factory import AdapterFactory, EnvironmentCredentialResolver, RouteAdapterFactory
from ai_creation_canvas.api.models import router as models_router
from ai_creation_canvas.api.session import router as session_router
from ai_creation_canvas.api.assets import router as assets_router
from ai_creation_canvas.api.jobs import router as jobs_router
from ai_creation_canvas.api.results import router as results_router
from ai_creation_canvas.api.auth import router as auth_router
from ai_creation_canvas.api.activity import router as activity_router
from ai_creation_canvas.api.admin import router as admin_router
from ai_creation_canvas.api.usage import router as usage_router
from ai_creation_canvas.api.projects import router as projects_router
from ai_creation_canvas.api.prompt_skills import router as prompt_skills_router
from ai_creation_canvas.api.comfy_workflows import router as comfy_workflows_router
from ai_creation_canvas.api._common import problem
from ai_creation_canvas.auth.local import LocalAuthService
from ai_creation_canvas.catalog import AssignedModelCatalog, GovernedModelCatalog, LogicalModelCatalog, ManagedRoutingRuntime, ProviderSubmissionBudget
from ai_creation_canvas.config import Settings, load_comfyui_service_declarations, load_service_declarations
from ai_creation_canvas.comfy.library import ComfyWorkflowLibrary
from ai_creation_canvas.comfy.service import AuthHeaderResolver, ComfyHttpWorkflowService
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.errors import ApiError, DomainError
from ai_creation_canvas.domain.models import PortalRole
from ai_creation_canvas.storage.sqlite import CanvasStore
from ai_creation_canvas.prompt_skills import PromptSkillService, load_prompt_skills
from ai_creation_canvas.coordination import LocalExecutionCoordinator, RedisExecutionCoordinator
from ai_creation_canvas.credential_pools import CredentialPoolLoader
from ai_creation_canvas.routing import RouteSelector


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


def create_app(settings: Settings, *, static_dir: Path | str | None = None, model_catalog: ModelCatalog | None = None, registry: AdapterRegistry | None = None, canvas_store: CanvasStore | None = None, portal_transport=None, prompt_skill_service: PromptSkillService | None = None, adapter_factory: AdapterFactory | None = None, execution_coordinator=None, managed_routing_runtime: ManagedRoutingRuntime | None = None, provider_submission_budget: ProviderSubmissionBudget | None = None, comfy_auth_header_resolver: AuthHeaderResolver | None = None) -> FastAPI:
    """Create a service with signed API access and a deliberately narrow SPA fallback."""
    app = FastAPI()
    injected_execution_coordinator = execution_coordinator is not None
    if settings.environment == "production" and managed_routing_runtime is not None:
        raise ValueError("managed production runtime cannot be injected")
    if managed_routing_runtime is not None and provider_submission_budget is not None:
        raise ValueError("provider submission budget is already owned by the managed runtime")
    if registry is None:
        registry = AdapterRegistry()
    if settings.identity_mode == "local" and settings.enable_demo_adapter:
        registry.register_generation(DemoGenerationAdapter())
    if settings.enable_ark_adapter:
        import os
        api_key = os.environ.get("ARK_API_KEY", "")
        if not api_key:
            raise ValueError("ARK_API_KEY is required when real Ark media is enabled")
        assert settings.ark_models_config_path is not None and settings.ark_models_config_root is not None
        for adapter in build_ark_adapters(api_key=api_key, data_dir=settings.data_dir, config_path=settings.ark_models_config_path, config_root=settings.ark_models_config_root):
            registry.register_generation(adapter)
    store = canvas_store or CanvasStore(settings.data_dir)
    active_managed_routes = tuple(route for route in store.list_model_routes(include_archived=False) if route.enabled)
    if settings.environment == "production" and (store.list_model_definitions() or active_managed_routes) and settings.redis_url is None:
        raise ValueError("Redis is required for governed production models")
    if settings.environment == "production" and active_managed_routes and settings.credential_pools_path is None:
        raise ValueError("credential pool configuration is required for managed production routes")
    if model_catalog is None:
        if settings.services_config_path is not None:
            declarations = load_service_declarations(settings.services_config_path, settings.services_config_root)
            client = PortalClient(settings.portal_base_url, allowed_mounts=tuple(item.mount for item in declarations), verify=settings.portal_ca_file or True, allowed_methods=("GET", "POST", "HEAD"), allow_loopback_http=settings.portal_allow_loopback_http, max_concurrency=settings.portal_max_concurrency, transport=portal_transport)
            for declaration in declarations:
                if declaration.capability == "portrait_asset":
                    adapter = PortalPortraitAdapter(PortraitDeclaration(declaration.service_id, declaration.mount, routes=declaration.routes), client)
                    registry.register_asset(adapter); registry.register_generation(adapter)
                else:
                    registry.register_generation(PortalJobsAdapter(declaration, client))
        model_catalog = ModelCatalog(registry)
        factory = adapter_factory or AdapterFactory(data_dir=settings.data_dir, credential_resolver=EnvironmentCredentialResolver(), asset_loader=_local_asset_loader(Path(settings.data_dir)))
        model_catalog = GovernedModelCatalog(model_catalog, store, registry, factory)
        adapter_factory = factory
    app.state.adapter_registry = registry
    app.state.canvas_store = store
    app.state.comfy_workflow_library = ComfyWorkflowLibrary(store)
    app.state.comfy_workflow_services = ()
    if settings.comfyui_services_config_path is not None:
        declarations = load_comfyui_service_declarations(
            settings.comfyui_services_config_path, settings.comfyui_services_config_root
        )
        services = []
        for declaration in declarations:
            adapter = ComfyHttpWorkflowService(declaration, auth_header_resolver=comfy_auth_header_resolver)
            registry.register_comfy_workflow(adapter)
            services.append(adapter)
        app.state.comfy_workflow_services = tuple(services)
    app.state.adapter_factory = adapter_factory
    app.state.settings = settings
    if prompt_skill_service is None:
        import os
        skill_path = Path(__file__).parents[1] / "config" / "prompt-skills.example.json"
        prompt_skill_service = PromptSkillService(
            load_prompt_skills(skill_path, skill_path.parent),
            model_id=settings.prompt_skill_model_id,
            api_key=os.environ.get("ARK_API_KEY") if settings.prompt_skill_model_id else None,
        )
    app.state.prompt_skill_service = prompt_skill_service
    if execution_coordinator is None:
        if settings.redis_url is not None:
            import os
            hmac_text = os.environ.get("AICC_CREDENTIAL_HMAC_KEY", "")
            hmac_key = hmac_text.encode("utf-8") if len(hmac_text.encode("utf-8")) >= 32 else None
            from redis.asyncio import Redis
            execution_coordinator = RedisExecutionCoordinator(
                Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3),
                namespace="aicc", global_limit=settings.generation_global_concurrency,
                provider_limit=settings.generation_provider_concurrency, user_limit=settings.generation_user_concurrency,
                credential_hmac_key=hmac_key,
            )
        else:
            execution_coordinator = LocalExecutionCoordinator(
                global_limit=settings.generation_global_concurrency,
                provider_limit=settings.generation_provider_concurrency,
                user_limit=settings.generation_user_concurrency,
            )
    app.state.execution_coordinator = execution_coordinator
    app.state.credential_pool_loader = None
    if managed_routing_runtime is None and settings.credential_pools_path is not None:
        import os
        if settings.environment == "production" and len(os.environ.get("AICC_CREDENTIAL_HMAC_KEY", "").encode("utf-8")) < 32:
            raise ValueError("a server-only credential HMAC key is required for managed production routes")
        loader = CredentialPoolLoader(Path(settings.credential_pools_path), production=settings.environment == "production")
        loader.load()
        app.state.credential_pool_loader = loader
        from ai_creation_canvas.trusted_routing import provider_protocol_for_definition
        providers = {}
        for provider in store.list_provider_definitions():
            protocol = provider_protocol_for_definition(provider)
            if protocol is not None:
                providers[provider.provider_id] = protocol
        if settings.environment == "production" and injected_execution_coordinator:
            raise ValueError("production execution coordinator cannot be injected")
        route_factory = RouteAdapterFactory(
            data_dir=settings.data_dir,
            asset_loader=_local_asset_loader(Path(settings.data_dir)),
            provider_protocols=providers,
        )
        managed_routing_runtime = ManagedRoutingRuntime(
            store, lambda: loader.reload().as_mapping(), RouteSelector(trusted_routes_only=True), execution_coordinator, route_factory,
            provider_submission_budget,
        )
    if managed_routing_runtime is not None:
        if managed_routing_runtime.store is not store:
            raise ValueError("managed routing runtime store does not match the app store")
        model_catalog = LogicalModelCatalog(model_catalog, store, managed_routing_runtime)
    app.state.model_catalog = AssignedModelCatalog(model_catalog, app.state.canvas_store) if settings.identity_mode == "local" else model_catalog
    app.state.managed_routing_runtime = managed_routing_runtime
    app.state.upload_semaphore = asyncio.Semaphore(settings.upload_concurrency)
    app.state.local_auth = LocalAuthService(app.state.canvas_store, session_ttl_seconds=settings.session_ttl_seconds) if settings.identity_mode == "local" else None
    build_dir = Path(static_dir) if static_dir is not None else Path(__file__).parents[2] / "web" / "dist"
    build_dir = build_dir.resolve(strict=False)

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        request.state.request_id = _request_id(request.headers.get("x-request-id"))
        try:
            if _is_api_v1_path(request.url.path):
                login_path = request.url.path == "/api/v1/auth/login" and request.method == "POST"
                if settings.identity_mode == "local":
                    token = request.cookies.get(settings.session_cookie_name, "")
                    request.state.local_session_token = token
                    user = app.state.local_auth.resolve(token) if token else None
                    if user is not None:
                        request.state.portal_user = user
                    elif not login_path:
                        raise AuthRequired(request.state.request_id)
                    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not login_path:
                        origin = request.headers.get("origin", "")
                        csrf = request.headers.get("x-csrf-token", "")
                        if origin not in settings.allowed_origins or not token or not app.state.local_auth.verify_csrf(token, csrf):
                            raise problem(request, "FORBIDDEN", "The request could not be completed.", status=403, phase="authentication")
                    password_change_allowed = (request.method, request.url.path) in {
                        ("POST", "/api/v1/auth/login"),
                        ("POST", "/api/v1/auth/logout"),
                        ("POST", "/api/v1/auth/change-password"),
                        ("GET", "/api/v1/session"),
                    }
                    if user is not None and not password_change_allowed:
                        details = app.state.local_auth.session_details(token)
                        if details is None:
                            raise AuthRequired(request.state.request_id)
                        if bool(details["must_change_password"]):
                            raise problem(
                                request,
                                "PASSWORD_CHANGE_REQUIRED",
                                "Change the initial password before continuing.",
                                status=403,
                                phase="authentication",
                            )
                else:
                    request.state.portal_user = verify_portal_identity(
                        request.headers,
                        settings.portal_internal_token,
                        max_age_seconds=settings.signature_ttl_seconds,
                    )
                if request.url.path.startswith("/api/v1/admin") and request.state.portal_user.role is not PortalRole.ADMIN:
                    raise problem(request, "API_NOT_FOUND", "The requested API resource was not found.", status=404)
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

    @app.exception_handler(StarletteHTTPException)
    async def api_http_error_handler(request: Request, error: StarletteHTTPException):
        if _is_api_v1_path(request.url.path):
            request_id = _request_id(getattr(request.state, "request_id", None))
            code = "API_NOT_FOUND" if error.status_code == 404 else "REQUEST_REJECTED"
            return JSONResponse(status_code=error.status_code, content=ApiError(code, "The request could not be completed.", False, request_id, "request").to_dict())
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    app.include_router(auth_router)
    app.include_router(activity_router)
    app.include_router(admin_router)
    app.include_router(usage_router)
    app.include_router(projects_router)
    app.include_router(session_router)
    app.include_router(models_router)
    app.include_router(prompt_skills_router)
    app.include_router(comfy_workflows_router)
    app.include_router(assets_router)
    app.include_router(jobs_router)
    app.include_router(results_router)

    @app.api_route("/api/v1", methods=["GET", "HEAD", "OPTIONS"], include_in_schema=False)
    @app.api_route("/api/v1/", methods=["GET", "HEAD", "OPTIONS"], include_in_schema=False)
    async def api_namespace_not_found(request: Request) -> JSONResponse:
        request_id = _request_id(getattr(request.state, "request_id", None))
        return JSONResponse(status_code=404, content=ApiError("API_NOT_FOUND", "The requested API resource was not found.", False, request_id, "request").to_dict())

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

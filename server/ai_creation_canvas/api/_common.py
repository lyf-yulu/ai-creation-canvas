from __future__ import annotations

from fastapi import Request

from ai_creation_canvas.adapters.portal.identity import AuthRequired
from ai_creation_canvas.domain.models import PortalUser, RequestContext
from ai_creation_canvas.errors import ApiError, DomainError


def context_for(request: Request) -> RequestContext:
    user = getattr(request.state, "portal_user", None)
    request_id = getattr(request.state, "request_id", "identity")
    if not isinstance(user, PortalUser):
        raise AuthRequired(request_id)
    return RequestContext(user=user, request_id=request_id, trace_id=request_id)


def problem(request: Request, code: str, message: str, *, status: int = 400, retryable: bool = False, phase: str = "request") -> DomainError:
    error = DomainError(ApiError(code, message, retryable, getattr(request.state, "request_id", "request"), phase))
    error.status_code = status
    return error

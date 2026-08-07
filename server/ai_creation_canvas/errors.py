"""Safe, stable errors returned by the application boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


def _required(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value


@dataclass(frozen=True, slots=True)
class ApiError:
    code: str
    message: str
    retryable: bool
    request_id: str
    phase: str

    def __post_init__(self) -> None:
        _required(self.code, "code")
        _required(self.message, "message")
        _required(self.request_id, "request_id")
        _required(self.phase, "phase")
        if not isinstance(self.retryable, bool):
            raise ValueError("retryable must be a bool")

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "request_id": self.request_id,
            "phase": self.phase,
        }


class DomainError(Exception):
    """Exception wrapper that never renders an internal exception or credential."""

    def __init__(self, api_error: ApiError, *, cause: Exception | None = None) -> None:
        self.api_error = api_error
        self.__cause__ = cause
        super().__init__(api_error.message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.api_error.code!r}, request_id={self.api_error.request_id!r})"


class AdapterRegistrationError(ValueError):
    """Raised when a trusted adapter does not satisfy a port contract."""

class PortalUpstreamError(Exception):
    def __init__(self, code: str = "UPSTREAM_UNAVAILABLE", *, retryable: bool, status_code: int = 502) -> None:
        self.code, self.retryable, self.status_code = code, retryable, status_code
        super().__init__(code)


class AdapterNotFoundError(DomainError):
    """Raised for a requested adapter that has not been registered."""


_UPSTREAM_ERROR_MAP: Final[dict[str, tuple[str, str]]] = {
    "auth_required": ("AUTH_REQUIRED", "Sign in is required."),
    "forbidden": ("FORBIDDEN", "You do not have access to this resource."),
    "asset_invalid": ("ASSET_INVALID", "The selected asset is invalid."),
    "model_unavailable": ("MODEL_UNAVAILABLE", "The selected model is unavailable."),
    "request_rejected": ("REQUEST_REJECTED", "The request was rejected."),
    "rate_limited": ("RATE_LIMITED", "The service is temporarily rate limited."),
    "upstream_timeout": ("UPSTREAM_TIMEOUT", "The generation service timed out."),
    "task_failed": ("TASK_FAILED", "The generation task failed."),
    "result_expired": ("RESULT_EXPIRED", "The generation result has expired."),
}


def map_upstream_error(
    *,
    code: str | None,
    message: str | None,
    retryable: bool,
    request_id: str,
    phase: str,
) -> ApiError:
    """Map untrusted provider details to the public contract, dropping raw messages."""
    del message
    safe_code, safe_message = _UPSTREAM_ERROR_MAP.get(
        (code or "").strip().lower(),
        ("INTERNAL_ERROR", "The request could not be completed."),
    )
    return ApiError(
        code=safe_code,
        message=safe_message,
        retryable=retryable,
        request_id=request_id,
        phase=phase,
    )

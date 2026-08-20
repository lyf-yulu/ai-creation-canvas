"""Safe submission outcomes for conservative route and credential retries."""
from __future__ import annotations

from enum import StrEnum
import re
from typing import Mapping

import httpx

from ai_creation_canvas.errors import InvalidUpstreamResult, PortalUpstreamError


_ADAPTER_TEMPLATES = frozenset({
    "ark.image.generate",
    "ark.image.edit",
    "ark.video.generate",
    "chiyun_openai_images.image.edit",
    "chiyun_gemini_images.image.edit",
})
_SAFE_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_PROVIDER_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_ARK_TASK_ID = re.compile(r"cgt-[A-Za-z0-9_-]{1,120}\Z")
_MAX_ERROR_DOCUMENT = 8 * 1024


class SubmissionDisposition(StrEnum):
    NOT_SUBMITTED = "not_submitted"
    REJECTED = "rejected"
    TEMPORARY_UNAVAILABLE = "temporary_unavailable"
    SUBMISSION_UNKNOWN = "submission_unknown"
    ACCEPTED = "accepted"


class SubmissionError(PortalUpstreamError):
    """A provider-boundary error containing only bounded, safe fields."""

    def __init__(
        self,
        disposition: SubmissionDisposition | str,
        code: str,
        *,
        adapter_template: str,
        status_code: int = 502,
        provider_task_id: str | None = None,
    ) -> None:
        try:
            parsed = SubmissionDisposition(disposition)
        except ValueError as error:
            raise ValueError("submission disposition is invalid") from error
        if not isinstance(code, str) or _SAFE_CODE.fullmatch(code) is None:
            raise ValueError("submission error code is invalid")
        if adapter_template not in _ADAPTER_TEMPLATES:
            raise ValueError("submission adapter template is invalid")
        if provider_task_id is not None and (
            not isinstance(provider_task_id, str)
            or _PROVIDER_TASK_ID.fullmatch(provider_task_id) is None
        ):
            raise ValueError("provider task ID is invalid")
        if (parsed is SubmissionDisposition.ACCEPTED) != (provider_task_id is not None):
            raise ValueError("accepted submissions require a provider task ID")
        if parsed is SubmissionDisposition.ACCEPTED and (
            adapter_template != "ark.video.generate"
            or provider_task_id is None
            or _ARK_TASK_ID.fullmatch(provider_task_id) is None
        ):
            raise ValueError("accepted submission template is invalid")
        if type(status_code) is not int or not 100 <= status_code <= 599:
            raise ValueError("submission status code is invalid")
        self.disposition = parsed
        self.adapter_template = adapter_template
        self.provider_task_id = provider_task_id
        super().__init__(
            code,
            retryable=parsed in {
                SubmissionDisposition.NOT_SUBMITTED,
                SubmissionDisposition.TEMPORARY_UNAVAILABLE,
            },
            status_code=status_code,
        )

    @property
    def safe_to_retry_elsewhere(self) -> bool:
        return self.disposition in {
            SubmissionDisposition.NOT_SUBMITTED,
            SubmissionDisposition.TEMPORARY_UNAVAILABLE,
        }

    def __repr__(self) -> str:
        return (
            f"SubmissionError(disposition={self.disposition.value!r}, "
            f"code={self.code!r}, adapter_template={self.adapter_template!r}, "
            f"provider_task_id={self.provider_task_id!r})"
        )


class RejectedSubmissionError(SubmissionError, ValueError):
    """Typed local rejection that remains compatible with legacy ValueError callers."""

    def __init__(self, safe_message: str, adapter_template: str) -> None:
        self._safe_message = safe_message
        super().__init__(
            SubmissionDisposition.REJECTED,
            "REQUEST_REJECTED",
            adapter_template=adapter_template,
            status_code=400,
        )

    def __str__(self) -> str:
        return self._safe_message


class UnknownSubmissionResult(SubmissionError, InvalidUpstreamResult):
    """Typed malformed success compatible with existing result validation callers."""

    def __init__(self, adapter_template: str) -> None:
        super().__init__(
            SubmissionDisposition.SUBMISSION_UNKNOWN,
            "INVALID_UPSTREAM_RESULT",
            adapter_template=adapter_template,
        )


def local_rejection(error: ValueError, adapter_template: str) -> RejectedSubmissionError:
    """Map implementation-owned validation text onto a small safe vocabulary."""
    message = str(error).lower()
    if "parameters" in message:
        safe = "Submission parameters are invalid"
    elif "audio inputs" in message:
        safe = "Submission audio inputs are invalid"
    elif "video reference" in message:
        safe = "Submission video reference inputs are invalid"
    elif "unsupported asset flow" in message:
        safe = "Submission uses an unsupported asset flow"
    elif "reference count" in message:
        safe = "Submission reference count is invalid"
    elif "reference image" in message:
        safe = "Submission reference images are invalid"
    elif "too large" in message:
        safe = "Submission input is too large"
    else:
        safe = "Submission request is invalid"
    return RejectedSubmissionError(safe, adapter_template)


def classify_submission_error(error: Exception, adapter_template: str) -> SubmissionDisposition:
    """Classify only verified adapter failures; unfamiliar cases fail closed."""
    if adapter_template not in _ADAPTER_TEMPLATES:
        return SubmissionDisposition.SUBMISSION_UNKNOWN
    if isinstance(error, SubmissionError):
        return error.disposition if error.adapter_template == adapter_template else SubmissionDisposition.SUBMISSION_UNKNOWN
    if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
        return SubmissionDisposition.NOT_SUBMITTED
    if isinstance(
        error,
        (
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.ReadError,
            httpx.WriteError,
            httpx.ProtocolError,
        ),
    ):
        return SubmissionDisposition.SUBMISSION_UNKNOWN
    if isinstance(error, PortalUpstreamError):
        if error.status_code in {408, 429} or error.status_code >= 500:
            return SubmissionDisposition.TEMPORARY_UNAVAILABLE
        return SubmissionDisposition.REJECTED
    if isinstance(error, InvalidUpstreamResult):
        return SubmissionDisposition.SUBMISSION_UNKNOWN
    if isinstance(error, ValueError):
        return SubmissionDisposition.REJECTED
    return SubmissionDisposition.SUBMISSION_UNKNOWN


def error_from_transport(error: httpx.HTTPError, adapter_template: str) -> SubmissionError:
    disposition = classify_submission_error(error, adapter_template)
    code = {
        SubmissionDisposition.NOT_SUBMITTED: "CONNECT_FAILED",
        SubmissionDisposition.SUBMISSION_UNKNOWN: "SUBMISSION_UNKNOWN",
    }.get(disposition, "SUBMISSION_UNKNOWN")
    return SubmissionError(disposition, code, adapter_template=adapter_template)


def error_from_response(response: httpx.Response, adapter_template: str) -> SubmissionError:
    if 400 <= response.status_code < 500 and response.status_code not in {408, 429}:
        return SubmissionError(
            SubmissionDisposition.REJECTED,
            "REQUEST_REJECTED",
            adapter_template=adapter_template,
            status_code=response.status_code,
        )
    if response.status_code in {408, 429}:
        return SubmissionError(
            SubmissionDisposition.TEMPORARY_UNAVAILABLE,
            "TEMPORARY_UNAVAILABLE",
            adapter_template=adapter_template,
            status_code=response.status_code,
        )
    if response.status_code >= 500:
        return SubmissionError(
            SubmissionDisposition.TEMPORARY_UNAVAILABLE,
            "TEMPORARY_UNAVAILABLE",
            adapter_template=adapter_template,
            status_code=response.status_code,
        )
    task_id = _verified_task_id(response, adapter_template)
    if task_id is not None:
        return SubmissionError(
            SubmissionDisposition.ACCEPTED,
            "PROVIDER_TASK_ACCEPTED",
            adapter_template=adapter_template,
            status_code=response.status_code,
            provider_task_id=task_id,
        )
    return SubmissionError(
        SubmissionDisposition.REJECTED,
        "REQUEST_REJECTED",
        adapter_template=adapter_template,
        status_code=response.status_code,
    )


def _verified_task_id(response: httpx.Response, adapter_template: str) -> str | None:
    if adapter_template != "ark.video.generate" or len(response.content) > _MAX_ERROR_DOCUMENT:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, Mapping):
        return None
    value = body.get("id") if isinstance(body.get("id"), str) else body.get("task_id")
    if not isinstance(value, str) or _PROVIDER_TASK_ID.fullmatch(value) is None:
        return None
    if _ARK_TASK_ID.fullmatch(value) is None:
        return None
    return value

import pytest

from ai_creation_canvas.errors import ApiError, DomainError, map_upstream_error


def test_api_error_carries_only_safe_client_fields():
    error = ApiError(
        code="UPSTREAM_TIMEOUT",
        message="The generation service timed out.",
        retryable=True,
        request_id="request-1",
        phase="polling",
    )

    assert error.to_dict() == {
        "code": "UPSTREAM_TIMEOUT",
        "message": "The generation service timed out.",
        "retryable": True,
        "request_id": "request-1",
        "phase": "polling",
    }


def test_domain_error_repr_does_not_include_internal_cause():
    cause = RuntimeError("provider secret=do-not-expose")
    error = DomainError(
        ApiError(
            code="INTERNAL_ERROR",
            message="The request could not be completed.",
            retryable=False,
            request_id="request-1",
            phase="submission",
        ),
        cause=cause,
    )

    assert "secret" not in repr(error)
    assert error.api_error.code == "INTERNAL_ERROR"


def test_maps_future_upstream_error_to_safe_contract_without_raw_message():
    mapped = map_upstream_error(
        code="rate_limited",
        message="authorization=super-secret",
        retryable=True,
        request_id="request-1",
        phase="submission",
    )

    assert mapped.code == "RATE_LIMITED"
    assert mapped.retryable is True
    assert mapped.request_id == "request-1"
    assert mapped.phase == "submission"
    assert "secret" not in mapped.message


def test_error_fields_require_non_empty_safe_identifiers():
    with pytest.raises(ValueError, match="request_id"):
        ApiError(
            code="TASK_FAILED",
            message="Task failed.",
            retryable=False,
            request_id=" ",
            phase="polling",
        )

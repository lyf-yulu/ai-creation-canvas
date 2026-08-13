from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import httpx
import pytest

from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration
from ai_creation_canvas.adapters.chiyun import ChiyunGenerationAdapter
from ai_creation_canvas.adapters.retry import SubmissionDisposition, SubmissionError, classify_submission_error
from ai_creation_canvas.domain.models import JobRequest, ModelInputPort, ModelOperation, PortalRole, PortalUser, RequestContext
from ai_creation_canvas.errors import InvalidUpstreamResult, PortalUpstreamError
from ai_creation_canvas.model_registry import GovernedModelDefinition, ModelModality, OperationContract, ProviderDefinition


PNG = b"\x89PNG\r\n\x1a\nresult"


def _context() -> RequestContext:
    return RequestContext(PortalUser("user-a", "Alice", PortalRole.USER), "request-a", "trace-a")


def _http_error(kind: type[httpx.HTTPError]) -> httpx.HTTPError:
    request = httpx.Request("POST", "https://provider.example/path")
    return kind("raw secret provider URL", request=request)


@pytest.mark.parametrize("adapter_template", ["ark.image.generate", "ark.video.generate", "chiyun_openai_images.image.edit"])
@pytest.mark.parametrize(
    ("error", "want"),
    [
        (_http_error(httpx.ConnectError), SubmissionDisposition.NOT_SUBMITTED),
        (_http_error(httpx.ConnectTimeout), SubmissionDisposition.NOT_SUBMITTED),
        (_http_error(httpx.ReadTimeout), SubmissionDisposition.SUBMISSION_UNKNOWN),
        (_http_error(httpx.WriteTimeout), SubmissionDisposition.SUBMISSION_UNKNOWN),
        (_http_error(httpx.ReadError), SubmissionDisposition.SUBMISSION_UNKNOWN),
        (_http_error(httpx.WriteError), SubmissionDisposition.SUBMISSION_UNKNOWN),
        (_http_error(httpx.RemoteProtocolError), SubmissionDisposition.SUBMISSION_UNKNOWN),
        (PortalUpstreamError("UPSTREAM_UNAVAILABLE", retryable=True, status_code=408), SubmissionDisposition.TEMPORARY_UNAVAILABLE),
        (PortalUpstreamError("UPSTREAM_UNAVAILABLE", retryable=True, status_code=429), SubmissionDisposition.TEMPORARY_UNAVAILABLE),
        (PortalUpstreamError("UPSTREAM_UNAVAILABLE", retryable=True, status_code=500), SubmissionDisposition.TEMPORARY_UNAVAILABLE),
        (PortalUpstreamError("REQUEST_REJECTED", retryable=False, status_code=401), SubmissionDisposition.REJECTED),
        (PortalUpstreamError("REQUEST_REJECTED", retryable=False, status_code=403), SubmissionDisposition.REJECTED),
        (PortalUpstreamError("INVALID_MODEL", retryable=False, status_code=400), SubmissionDisposition.REJECTED),
        (PortalUpstreamError("INVALID_PARAMETER", retryable=False, status_code=422), SubmissionDisposition.REJECTED),
        (PortalUpstreamError("CONTENT_REJECTED", retryable=False, status_code=400), SubmissionDisposition.REJECTED),
        (ValueError("local validation"), SubmissionDisposition.REJECTED),
        (InvalidUpstreamResult("malformed success"), SubmissionDisposition.SUBMISSION_UNKNOWN),
        (RuntimeError("unknown"), SubmissionDisposition.SUBMISSION_UNKNOWN),
    ],
)
def test_retry_classifier_is_conservative_for_each_enabled_template(adapter_template: str, error: Exception, want: SubmissionDisposition) -> None:
    assert classify_submission_error(error, adapter_template) is want


def test_only_definitely_unsubmitted_or_explicitly_temporary_errors_retry_elsewhere() -> None:
    expected = {
        SubmissionDisposition.NOT_SUBMITTED: True,
        SubmissionDisposition.TEMPORARY_UNAVAILABLE: True,
        SubmissionDisposition.REJECTED: False,
        SubmissionDisposition.SUBMISSION_UNKNOWN: False,
        SubmissionDisposition.ACCEPTED: False,
    }
    for disposition, safe in expected.items():
        error = SubmissionError(
            disposition,
            "SAFE_CODE",
            adapter_template="ark.video.generate",
            provider_task_id="cgt-task-safe-1" if disposition is SubmissionDisposition.ACCEPTED else None,
        )
        assert error.safe_to_retry_elsewhere is safe
        assert error.retryable is safe
    assert classify_submission_error(RuntimeError("unknown"), "not-allowlisted") is SubmissionDisposition.SUBMISSION_UNKNOWN


def test_verified_provider_task_id_is_accepted_only_for_ark_async_video_template() -> None:
    error = SubmissionError(
        SubmissionDisposition.ACCEPTED,
        "PROVIDER_TASK_ACCEPTED",
        adapter_template="ark.video.generate",
        status_code=503,
        provider_task_id="cgt-task-safe-1",
    )
    assert classify_submission_error(error, "ark.video.generate") is SubmissionDisposition.ACCEPTED
    assert classify_submission_error(error, "ark.image.generate") is SubmissionDisposition.SUBMISSION_UNKNOWN
    assert error.provider_task_id == "cgt-task-safe-1"
    with pytest.raises(ValueError):
        SubmissionError(
            SubmissionDisposition.ACCEPTED,
            "PROVIDER_TASK_ACCEPTED",
            adapter_template="chiyun_openai_images.image.edit",
            provider_task_id="task-safe-1",
        )


def _ark_adapter(tmp_path: Path, transport: httpx.AsyncBaseTransport) -> ArkGenerationAdapter:
    return ArkGenerationAdapter(
        api_key="adapter-secret",
        data_dir=tmp_path,
        models=(ArkModelDeclaration("logical-image", "route-ark", "Image", ("image.generate",), {"type": "object", "properties": {}, "additionalProperties": False}, provider_model_name="ep-real-model"),),
        transport=transport,
    )


def _ark_video_adapter(tmp_path: Path, transport: httpx.AsyncBaseTransport) -> ArkGenerationAdapter:
    return ArkGenerationAdapter(
        api_key="adapter-secret",
        data_dir=tmp_path,
        models=(ArkModelDeclaration("logical-video", "route-ark", "Video", ("video.generate",), {"type": "object", "properties": {}, "additionalProperties": False}, provider_model_name="ep-real-video"),),
        transport=transport,
    )


def _chiyun_adapter(tmp_path: Path, transport: httpx.AsyncBaseTransport) -> ChiyunGenerationAdapter:
    provider = ProviderDefinition("route-chiyun", "Managed route", "chiyun_openai_images", "https://trusted.example", "lease")
    model = GovernedModelDefinition(
        "logical-image", "route-chiyun", "gpt-image-2", "Image", "Edit", ModelModality.IMAGE,
        (OperationContract(
            ModelOperation.IMAGE_EDIT,
            (ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 1, 10)),
            "image",
            {"type": "object", "properties": {"size": {"type": "string"}, "output_count": {"type": "integer"}}, "additionalProperties": False},
            {"size": "size", "output_count": "n"},
        ),),
    )
    return ChiyunGenerationAdapter(
        provider=provider,
        models=(model,),
        api_key="adapter-secret",
        data_dir=tmp_path,
        asset_loader=lambda _: (PNG, "image/png"),
        transport=transport,
    )


async def _submit(adapter_template: str, tmp_path: Path, transport: httpx.AsyncBaseTransport) -> None:
    if adapter_template == "ark.image.generate":
        adapter = _ark_adapter(tmp_path, transport)
        await adapter.submit(_context(), JobRequest(ModelOperation.IMAGE_GENERATE, "logical-image", "private prompt", "one"))
        return
    if adapter_template == "ark.video.generate":
        adapter = _ark_video_adapter(tmp_path, transport)
        await adapter.submit(_context(), JobRequest(ModelOperation.VIDEO_GENERATE, "logical-video", "private prompt", "one"))
        return
    adapter = _chiyun_adapter(tmp_path, transport)
    await adapter.submit(_context(), JobRequest(
        ModelOperation.IMAGE_EDIT,
        "logical-image",
        "private prompt",
        "one",
        {"size": "1024x1024", "output_count": 1},
        inputs={"reference_images": ("ref",)},
    ))


@pytest.mark.parametrize("adapter_template", ["ark.image.generate", "ark.video.generate", "chiyun_openai_images.image.edit"])
@pytest.mark.parametrize(
    ("status", "want"),
    [
        (408, SubmissionDisposition.TEMPORARY_UNAVAILABLE),
        (429, SubmissionDisposition.TEMPORARY_UNAVAILABLE),
        (401, SubmissionDisposition.REJECTED),
        (403, SubmissionDisposition.REJECTED),
        (400, SubmissionDisposition.REJECTED),
        (503, SubmissionDisposition.TEMPORARY_UNAVAILABLE),
    ],
)
def test_submission_http_responses_raise_safe_typed_dispositions(tmp_path: Path, adapter_template: str, status: int, want: SubmissionDisposition) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(status, text="raw-body adapter-secret private prompt https://secret.example"))
    with pytest.raises(SubmissionError) as caught:
        asyncio.run(_submit(adapter_template, tmp_path, transport))
    error = caught.value
    assert error.disposition is want
    assert classify_submission_error(error, adapter_template) is want
    assert "raw-body" not in str(error)
    assert "adapter-secret" not in str(error)
    assert "private prompt" not in repr(error)
    assert "secret.example" not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize(
    ("exception_type", "want"),
    [
        (httpx.ConnectError, SubmissionDisposition.NOT_SUBMITTED),
        (httpx.ConnectTimeout, SubmissionDisposition.NOT_SUBMITTED),
        (httpx.ReadTimeout, SubmissionDisposition.SUBMISSION_UNKNOWN),
        (httpx.WriteTimeout, SubmissionDisposition.SUBMISSION_UNKNOWN),
        (httpx.RemoteProtocolError, SubmissionDisposition.SUBMISSION_UNKNOWN),
    ],
)
@pytest.mark.parametrize("adapter_template", ["ark.image.generate", "ark.video.generate", "chiyun_openai_images.image.edit"])
def test_submission_transport_failures_raise_safe_typed_dispositions(
    tmp_path: Path,
    adapter_template: str,
    exception_type: type[httpx.HTTPError],
    want: SubmissionDisposition,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exception_type("raw adapter-secret https://secret.example", request=request)

    with pytest.raises(SubmissionError) as caught:
        asyncio.run(_submit(adapter_template, tmp_path, httpx.MockTransport(handler)))
    assert caught.value.disposition is want
    assert "adapter-secret" not in repr(caught.value)
    assert "secret.example" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize("adapter_template", ["ark.image.generate", "chiyun_openai_images.image.edit"])
def test_synchronous_template_explicit_5xx_is_retryable_even_if_body_contains_an_irrelevant_task_id(tmp_path: Path, adapter_template: str) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(503, json={"id": "cgt-not-an-async-task"}))
    with pytest.raises(SubmissionError) as caught:
        asyncio.run(_submit(adapter_template, tmp_path, transport))
    assert caught.value.disposition is SubmissionDisposition.TEMPORARY_UNAVAILABLE
    assert caught.value.provider_task_id is None
    assert caught.value.safe_to_retry_elsewhere is True


def test_ark_async_video_explicit_5xx_is_retryable_even_if_body_contains_a_task_id(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(503, json={"id": "cgt-recoverable"}))
    with pytest.raises(SubmissionError) as caught:
        asyncio.run(_submit("ark.video.generate", tmp_path, transport))
    assert caught.value.disposition is SubmissionDisposition.TEMPORARY_UNAVAILABLE
    assert caught.value.provider_task_id is None
    assert caught.value.safe_to_retry_elsewhere is True


def test_ark_async_video_business_4xx_never_becomes_accepted_from_a_body_task_id(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(400, json={"id": "cgt-must-not-bypass-rejection"}))
    with pytest.raises(SubmissionError) as caught:
        asyncio.run(_submit("ark.video.generate", tmp_path, transport))
    assert caught.value.disposition is SubmissionDisposition.REJECTED
    assert caught.value.provider_task_id is None
    assert caught.value.safe_to_retry_elsewhere is False


@pytest.mark.parametrize("adapter_template", ["ark.image.generate", "chiyun_openai_images.image.edit"])
def test_local_validation_is_a_typed_rejection_before_transport(tmp_path: Path, adapter_template: str) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    if adapter_template == "ark.image.generate":
        adapter = _ark_adapter(tmp_path, httpx.MockTransport(handler))
        request = JobRequest(ModelOperation.VIDEO_GENERATE, "logical-image", "private prompt", "invalid")
    else:
        adapter = _chiyun_adapter(tmp_path, httpx.MockTransport(handler))
        request = JobRequest(ModelOperation.IMAGE_EDIT, "logical-image", "private prompt", "invalid", {"size": "1024x1024", "output_count": 1})
    with pytest.raises(SubmissionError) as caught:
        asyncio.run(adapter.submit(_context(), request))
    assert caught.value.disposition is SubmissionDisposition.REJECTED
    assert isinstance(caught.value, ValueError)
    assert calls == 0
    assert "private prompt" not in repr(caught.value)


@pytest.mark.parametrize("adapter_template", ["ark.image.generate", "ark.video.generate", "chiyun_openai_images.image.edit"])
def test_malformed_success_is_typed_as_submission_unknown(tmp_path: Path, adapter_template: str) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"raw malformed adapter-secret"))
    with pytest.raises(SubmissionError) as caught:
        asyncio.run(_submit(adapter_template, tmp_path, transport))
    assert caught.value.disposition is SubmissionDisposition.SUBMISSION_UNKNOWN
    assert isinstance(caught.value, InvalidUpstreamResult)
    assert "raw malformed" not in str(caught.value)
    assert "adapter-secret" not in repr(caught.value)


def _assert_safe_unknown(error: SubmissionError) -> None:
    assert error.disposition is SubmissionDisposition.SUBMISSION_UNKNOWN
    assert error.safe_to_retry_elsewhere is False
    assert error.retryable is False
    assert error.provider_task_id is None
    assert "/private/sensitive" not in str(error)
    assert "adapter-secret" not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_ark_provider_success_then_pending_index_failure_is_safe_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [{"url": "https://download.volces.com/result.png"}]}))
    adapter = _ark_adapter(tmp_path, transport)

    def fail_replace(_: object, __: object) -> None:
        raise OSError("/private/sensitive adapter-secret")

    monkeypatch.setattr("ai_creation_canvas.adapters.ark.os.replace", fail_replace)
    with pytest.raises(SubmissionError) as caught:
        asyncio.run(adapter.submit(_context(), JobRequest(ModelOperation.IMAGE_GENERATE, "logical-image", "private prompt", "index-fail")))
    _assert_safe_unknown(caught.value)
    assert not (tmp_path / "ark-results" / "pending.tmp").exists()


def test_ark_provider_success_then_result_download_storage_failure_is_safe_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://download.volces.com/result.png"}]})
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)

    adapter = _ark_adapter(tmp_path, httpx.MockTransport(handler))
    submitted = asyncio.run(adapter.submit(_context(), JobRequest(ModelOperation.IMAGE_GENERATE, "logical-image", "private prompt", "download-fail")))

    def fail_replace(_: object, __: object) -> None:
        raise OSError("/private/sensitive adapter-secret")

    monkeypatch.setattr("ai_creation_canvas.adapters.ark.os.replace", fail_replace)
    with pytest.raises(SubmissionError) as caught:
        asyncio.run(adapter.poll(_context(), submitted.upstream_job_id))
    _assert_safe_unknown(caught.value)
    assert not tuple((tmp_path / "ark-results").glob(".ark_result_*.tmp"))


@pytest.mark.parametrize("failure_point", ["result", "index"])
def test_chiyun_provider_success_then_local_materialization_failure_is_safe_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    adapter = _chiyun_adapter(
        tmp_path,
        httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})),
    )
    if failure_point == "result":
        def fail_replace(_: object, __: object) -> None:
            raise OSError("/private/sensitive adapter-secret")

        monkeypatch.setattr("ai_creation_canvas.adapters.chiyun.os.replace", fail_replace)
    else:
        def fail_index(_: object) -> None:
            raise OSError("/private/sensitive adapter-secret")

        monkeypatch.setattr(adapter, "_write_index", fail_index)
    with pytest.raises(SubmissionError) as caught:
        asyncio.run(adapter.submit(_context(), JobRequest(
            ModelOperation.IMAGE_EDIT,
            "logical-image",
            "private prompt",
            "local-fail",
            {"size": "1024x1024", "output_count": 1},
            inputs={"reference_images": ("ref",)},
        )))
    _assert_safe_unknown(caught.value)
    result_root = tmp_path / "chiyun-results"
    assert not tuple(result_root.glob(".chiyun_result_*.tmp"))
    assert not tuple(path for path in result_root.glob("chiyun_result_*") if path.is_file())

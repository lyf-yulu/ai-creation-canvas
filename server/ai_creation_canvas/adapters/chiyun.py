"""Trusted Chiyun OpenAI-images adapter for governed image-edit models."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Callable, Mapping

import httpx

from ai_creation_canvas.domain.models import AssetRef, JobRequest, JobState, JobStatus, ModelOperation, ModelSpec, RequestContext, UpstreamJob
from ai_creation_canvas.errors import InvalidUpstreamResult, PortalUpstreamError
from ai_creation_canvas.adapters.retry import SubmissionError, SubmissionDisposition, UnknownSubmissionResult, error_from_response, error_from_transport, local_rejection
from ai_creation_canvas.model_registry import GovernedModelDefinition, ProviderDefinition
from ai_creation_canvas.parameter_schema import validate_parameter_values


_IMAGE_MIME = frozenset({"image/png", "image/jpeg", "image/webp"})
_MAX_INPUT_ITEM = 32 * 1024 * 1024
_MAX_INPUT_TOTAL = 64 * 1024 * 1024
_MAX_RESPONSE = 64 * 1024 * 1024
_MAX_RESULT = 32 * 1024 * 1024
_RESULT_ID = re.compile(r"chiyun_result_[0-9a-f]{64}\Z")
_UPSTREAM_ID = re.compile(r"chiyun_[0-9a-f]{64}\Z")


class _FileStream:
    def __init__(self, path: Path, mime: str, *, offset: int = 0, length: int | None = None, head: bool = False) -> None:
        size = path.stat().st_size
        self.status_code = 200 if offset == 0 and length is None else 206
        self.headers = {"content-type": mime, "content-length": str(size if length is None else length), "accept-ranges": "bytes", "cache-control": "no-store"}
        if self.status_code == 206:
            assert length is not None
            self.headers["content-range"] = f"bytes {offset}-{offset + length - 1}/{size}"
        self._path, self._offset, self._length, self._head = path, offset, length, head

    async def aiter_bytes(self):
        if self._head:
            return
        remaining = self._length
        with self._path.open("rb") as source:
            source.seek(self._offset)
            while remaining is None or remaining > 0:
                chunk = await asyncio.to_thread(source.read, min(64 * 1024, remaining) if remaining is not None else 64 * 1024)
                if not chunk:
                    return
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk

    async def aclose(self) -> None:
        return None


class ChiyunGenerationAdapter:
    requires_portal_cookie = False

    def __init__(self, *, provider: ProviderDefinition, models: tuple[GovernedModelDefinition, ...], api_key: str, data_dir: Path | str, asset_loader: Callable[[str], tuple[bytes, str]], transport: httpx.AsyncBaseTransport | None = None) -> None:
        if provider.adapter_type != "chiyun_openai_images" or not provider.enabled or not isinstance(api_key, str) or len(api_key.strip()) < 8:
            raise ValueError("Chiyun provider is unavailable")
        if not models or any(model.provider_id != provider.provider_id or not model.enabled or len(model.operation_contracts) != 1 or model.operation_contracts[0].operation is not ModelOperation.IMAGE_EDIT for model in models):
            raise ValueError("Chiyun model definitions are invalid")
        if len({model.model_id for model in models}) != len(models) or not callable(asset_loader):
            raise ValueError("Chiyun model definitions are invalid")
        self.service_id = provider.provider_id
        self._provider, self._models, self._api_key = provider, {model.model_id: model for model in models}, api_key.strip()
        self._asset_loader, self._transport = asset_loader, transport
        self.reusable_result_services = frozenset({self.service_id})
        self._root = Path(data_dir) / "chiyun-results"
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self._root.is_symlink():
            raise ValueError("Chiyun result root is unsafe")
        os.chmod(self._root, 0o700)
        self._index = self._root / "pending.json"
        self._lock = asyncio.Lock()

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._models))

    async def list_models(self, context: RequestContext) -> tuple[ModelSpec, ...]:
        del context
        return tuple(self._models[model_id].model_spec(self.service_id) for model_id in self.model_ids)

    async def submit(self, context: RequestContext, request: JobRequest) -> UpstreamJob:
        typed_error: SubmissionError | None = None
        try:
            return await self._submit(context, request)
        except SubmissionError:
            raise
        except InvalidUpstreamResult:
            typed_error = UnknownSubmissionResult()
        except ValueError as error:
            typed_error = local_rejection(error)
        raise typed_error

    async def _submit(self, context: RequestContext, request: JobRequest) -> UpstreamJob:
        del context
        model = self._models.get(request.model_id)
        if model is None or request.operation is not ModelOperation.IMAGE_EDIT or request.asset_ids or set(request.inputs) != {"reference_images"}:
            raise ValueError("Chiyun request is outside the model contract")
        contract = model.operation_contracts[0]
        refs = tuple(request.inputs["reference_images"])
        port = next(port for port in contract.input_ports if port.port_id == "reference_images")
        if not port.min_items <= len(refs) <= port.max_items:
            raise ValueError("Chiyun reference count is invalid")
        try:
            params = validate_parameter_values(contract.parameter_schema, request.params)
        except ValueError as error:
            raise ValueError("Chiyun parameters are invalid") from error
        contents: list[tuple[bytes, str]] = []
        total = 0
        for asset_id in refs:
            body, mime = self._asset_loader(asset_id)
            if mime not in _IMAGE_MIME or not isinstance(body, bytes) or not body or len(body) > _MAX_INPUT_ITEM:
                raise ValueError("Chiyun reference image is invalid")
            total += len(body)
            if total > _MAX_INPUT_TOTAL:
                raise ValueError("Chiyun reference images are too large")
            contents.append((body, mime))
        files = [("image[]", (f"reference-{index:03d}.{_extension(mime)}", body, mime)) for index, (body, mime) in enumerate(contents)]
        data = {
            "model": model.provider_model_name,
            "prompt": request.prompt,
            "size": str(params["size"]),
            "n": str(params["output_count"]),
        }
        payload = await self._post(data, files)
        results = _decode_results(payload, expected=int(params["output_count"]))
        upstream_id = "chiyun_" + hashlib.sha256(f"{request.model_id}\n{request.idempotency_key}".encode()).hexdigest()
        result_ids = tuple(self._store_result(upstream_id, index, body) for index, body in enumerate(results))
        async with self._lock:
            values = self._read_index()
            values[upstream_id] = list(result_ids)
            self._write_index(values)
        return UpstreamJob(self.service_id, upstream_id, JobState(upstream_id, JobStatus.QUEUED))

    async def poll(self, context: RequestContext, upstream_job_id: str) -> JobState:
        del context
        if _UPSTREAM_ID.fullmatch(upstream_job_id) is None:
            raise ValueError("Chiyun job is invalid")
        async with self._lock:
            values = self._read_index()
            raw = values.pop(upstream_job_id, None)
            if raw is not None:
                self._write_index(values)
        if not isinstance(raw, list) or not 1 <= len(raw) <= 4 or any(not isinstance(item, str) or _RESULT_ID.fullmatch(item) is None for item in raw):
            raise InvalidUpstreamResult("Chiyun result index is invalid")
        results = tuple(AssetRef(item, "reference", "active", "image/png", "image") for item in raw)
        return JobState(upstream_job_id, JobStatus.SUCCEEDED, results=results)

    async def open_result(self, context: RequestContext, result_id: str, *, cookie_header: str, range_header: str | None = None, head: bool = False):
        del context, cookie_header
        if _RESULT_ID.fullmatch(result_id) is None:
            return _empty_stream(404)
        path = self._root / result_id
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= _MAX_RESULT:
            return _empty_stream(404)
        if range_header is None:
            return _FileStream(path, "image/png", head=head)
        interval = _range(range_header, path.stat().st_size)
        if interval is None:
            return _empty_stream(416, size=path.stat().st_size)
        start, end = interval
        return _FileStream(path, "image/png", offset=start, length=end - start + 1, head=head)

    async def _post(self, data: Mapping[str, str], files: list[tuple[str, tuple[str, bytes, str]]]) -> Mapping[str, object]:
        submission_error: SubmissionError | None = None
        try:
            async with httpx.AsyncClient(base_url=self._provider.base_url, transport=self._transport, timeout=httpx.Timeout(180, connect=10), follow_redirects=False, trust_env=False) as client:
                async with client.stream("POST", "/v1/images/edits", headers={"Authorization": f"Bearer {self._api_key}"}, data=data, files=files) as response:
                    if not 200 <= response.status_code < 300:
                        error_body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(error_body) + len(chunk) > 8 * 1024:
                                error_body.clear()
                                break
                            error_body.extend(chunk)
                        safe_response = httpx.Response(response.status_code, content=bytes(error_body))
                        raise error_from_response(safe_response, "chiyun_openai_images")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > _MAX_RESPONSE:
                            raise InvalidUpstreamResult("Chiyun response is too large")
                        raw.extend(chunk)
        except (SubmissionError, InvalidUpstreamResult):
            raise
        except httpx.HTTPError as error:
            submission_error = error_from_transport(error, "chiyun_openai_images")
        except OSError:
            submission_error = SubmissionError(SubmissionDisposition.SUBMISSION_UNKNOWN, "SUBMISSION_UNKNOWN")
        if submission_error is not None:
            raise submission_error
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise InvalidUpstreamResult("Chiyun response is invalid") from error
        if not isinstance(payload, Mapping):
            raise InvalidUpstreamResult("Chiyun response is invalid")
        return payload

    def _store_result(self, upstream_id: str, index: int, body: bytes) -> str:
        result_id = "chiyun_result_" + hashlib.sha256(f"{upstream_id}\n{index}".encode()).hexdigest()
        destination, temporary = self._root / result_id, self._root / f".{result_id}.tmp"
        try:
            with temporary.open("xb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return result_id

    def _read_index(self) -> dict[str, object]:
        if not self._index.exists():
            return {}
        try:
            body = json.loads(self._index.read_text(encoding="ascii"))
        except (OSError, ValueError, UnicodeError):
            return {}
        return body if isinstance(body, dict) else {}

    def _write_index(self, values: Mapping[str, object]) -> None:
        temporary = self._root / ".pending.tmp"
        temporary.write_text(json.dumps(values, sort_keys=True, separators=(",", ":")), encoding="ascii")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._index)


def _decode_results(payload: Mapping[str, object], *, expected: int) -> tuple[bytes, ...]:
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != expected or not 1 <= len(data) <= 4:
        raise InvalidUpstreamResult("Chiyun result count is invalid")
    results: list[bytes] = []
    for item in data:
        if not isinstance(item, Mapping):
            raise InvalidUpstreamResult("Chiyun result is invalid")
        encoded = item.get("b64_json")
        if not isinstance(encoded, str) or not encoded or "url" in item:
            # URL results need a separate SSRF-safe downloader policy. Fail closed in this slice.
            raise InvalidUpstreamResult("Chiyun result is invalid")
        try:
            body = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True)
        except (binascii.Error, ValueError) as error:
            raise InvalidUpstreamResult("Chiyun result is invalid") from error
        if not 8 <= len(body) <= _MAX_RESULT or not body.startswith(b"\x89PNG\r\n\x1a\n"):
            raise InvalidUpstreamResult("Chiyun result is invalid")
        results.append(body)
    return tuple(results)


def _extension(mime: str) -> str:
    return {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime]


def _range(value: str, size: int) -> tuple[int, int] | None:
    if not value.startswith("bytes=") or "," in value:
        return None
    left, separator, right = value[6:].partition("-")
    if not separator or (not left and not right) or (left and not left.isdecimal()) or (right and not right.isdecimal()):
        return None
    if not left:
        suffix = int(right)
        return None if suffix <= 0 else (max(0, size - suffix), size - 1)
    start = int(left)
    if start >= size:
        return None
    end = min(int(right), size - 1) if right else size - 1
    return None if end < start else (start, end)


def _empty_stream(status: int, *, size: int | None = None):
    stream = _FileStream.__new__(_FileStream)
    stream.status_code = status
    stream.headers = {"content-length": "0", "cache-control": "no-store"}
    if status == 416 and size is not None:
        stream.headers["content-range"] = f"bytes */{size}"
    async def empty():
        if False:
            yield b""
    stream.aiter_bytes = empty  # type: ignore[attr-defined]
    stream.aclose = _noop  # type: ignore[attr-defined]
    return stream


async def _noop() -> None:
    return None

"""Trusted Chiyun Gemini image adapter with ordered inline references."""
from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Callable, Mapping
from urllib.parse import quote

import httpx

from ai_creation_canvas.adapters.chiyun import _FileStream, _empty_stream, _range, _safe_unlink
from ai_creation_canvas.adapters.retry import SubmissionError, SubmissionDisposition, UnknownSubmissionResult, error_from_response, error_from_transport, local_rejection
from ai_creation_canvas.domain.models import AssetRef, JobRequest, JobState, JobStatus, ModelOperation, ModelSpec, RequestContext, UpstreamJob
from ai_creation_canvas.errors import InvalidUpstreamResult, LocalRecoveryUnavailable
from ai_creation_canvas.model_registry import GovernedModelDefinition, ProviderDefinition
from ai_creation_canvas.parameter_schema import validate_parameter_values


_TEMPLATE = "chiyun_gemini_images.image.edit"
_IMAGE_MIME = frozenset({"image/png", "image/jpeg", "image/webp"})
_MAX_INPUT_ITEM = 32 * 1024 * 1024
_MAX_INPUT_TOTAL = 64 * 1024 * 1024
_MAX_RESPONSE = 64 * 1024 * 1024
_MAX_RESULT = 32 * 1024 * 1024
_RESULT_ID = re.compile(r"chiyun_gemini_result_[0-9a-f]{64}\Z")
_UPSTREAM_ID = re.compile(r"chiyun_gemini_[0-9a-f]{64}\Z")


class ChiyunGeminiGenerationAdapter:
    requires_portal_cookie = False
    supports_background_polling = True

    def __init__(self, *, provider: ProviderDefinition, models: tuple[GovernedModelDefinition, ...], api_key: str, data_dir: Path | str, asset_loader: Callable[[str], tuple[bytes, str]], transport: httpx.AsyncBaseTransport | None = None) -> None:
        if provider.adapter_type != "chiyun_gemini_images" or not provider.enabled or not isinstance(api_key, str) or len(api_key.strip()) < 8:
            raise ValueError("Chiyun Gemini provider is unavailable")
        if not models or any(model.provider_id != provider.provider_id or not model.enabled or len(model.operation_contracts) != 1 or model.operation_contracts[0].operation is not ModelOperation.IMAGE_EDIT for model in models):
            raise ValueError("Chiyun Gemini model definitions are invalid")
        if len({model.model_id for model in models}) != len(models) or not callable(asset_loader):
            raise ValueError("Chiyun Gemini model definitions are invalid")
        self.service_id = provider.provider_id
        self._provider, self._models, self._api_key = provider, {item.model_id: item for item in models}, api_key.strip()
        self._asset_loader, self._transport = asset_loader, transport
        self.reusable_result_services = frozenset({self.service_id})
        self._root = Path(data_dir) / "chiyun-gemini-results"
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self._root.is_symlink():
            raise ValueError("Chiyun Gemini result root is unsafe")
        os.chmod(self._root, 0o700)
        self._index = self._root / "pending.json"
        self._index_lock_path = self._root / "pending.lock"

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._models))

    async def list_models(self, context: RequestContext) -> tuple[ModelSpec, ...]:
        del context
        return tuple(self._models[item].model_spec(self.service_id) for item in self.model_ids)

    async def submit(self, context: RequestContext, request: JobRequest) -> UpstreamJob:
        try:
            return await self._submit(context, request)
        except SubmissionError:
            raise
        except InvalidUpstreamResult:
            raise UnknownSubmissionResult(_TEMPLATE)
        except ValueError as error:
            raise local_rejection(error, _TEMPLATE)
        except OSError:
            raise SubmissionError(SubmissionDisposition.SUBMISSION_UNKNOWN, "LOCAL_STATE_UNAVAILABLE", adapter_template=_TEMPLATE)

    async def _submit(self, context: RequestContext, request: JobRequest) -> UpstreamJob:
        del context
        model = self._models.get(request.model_id)
        if model is None or request.operation is not ModelOperation.IMAGE_EDIT or request.asset_ids or set(request.inputs) != {"reference_images"}:
            raise ValueError("Chiyun Gemini request is outside the model contract")
        contract = model.operation_contracts[0]
        references = tuple(request.inputs["reference_images"])
        port = next(item for item in contract.input_ports if item.port_id == "reference_images")
        if not port.min_items <= len(references) <= port.max_items:
            raise ValueError("Chiyun Gemini reference count is invalid")
        params = validate_parameter_values(contract.parameter_schema, request.params)
        parts: list[dict[str, object]] = [{"text": request.prompt}]
        total = 0
        for asset_id in references:
            body, mime = self._asset_loader(asset_id)
            if mime not in _IMAGE_MIME or not isinstance(body, bytes) or not body or len(body) > _MAX_INPUT_ITEM:
                raise ValueError("Chiyun Gemini reference image is invalid")
            total += len(body)
            if total > _MAX_INPUT_TOTAL:
                raise ValueError("Chiyun Gemini reference images are too large")
            parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(body).decode("ascii")}})
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"imageConfig": {"aspectRatio": params["aspect_ratio"], "imageSize": params["image_size"]}},
        }
        response = await self._post(model.provider_model_name, payload)
        body, mime = _decode_result(response)
        upstream_id = "chiyun_gemini_" + hashlib.sha256(f"{request.model_id}\n{request.idempotency_key}".encode()).hexdigest()
        result_id = "chiyun_gemini_result_" + hashlib.sha256(upstream_id.encode()).hexdigest()
        destination, temporary = self._root / result_id, self._root / f".{result_id}.tmp"
        try:
            with temporary.open("xb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
            with self._locked_index():
                values = self._read_index()
                values[upstream_id] = {"result_id": result_id, "mime": mime}
                self._write_index(values)
        finally:
            _safe_unlink(temporary)
        return UpstreamJob(self.service_id, upstream_id, JobState(upstream_id, JobStatus.QUEUED))

    async def poll(self, context: RequestContext, upstream_job_id: str) -> JobState:
        try:
            return await self._poll(context, upstream_job_id)
        except OSError as error:
            raise LocalRecoveryUnavailable() from error

    async def _poll(self, context: RequestContext, upstream_job_id: str) -> JobState:
        del context
        if _UPSTREAM_ID.fullmatch(upstream_job_id) is None:
            raise ValueError("Chiyun Gemini job is invalid")
        with self._locked_index():
            raw = self._read_index().get(upstream_job_id)
        if not isinstance(raw, dict) or not isinstance(raw.get("result_id"), str) or _RESULT_ID.fullmatch(raw["result_id"]) is None or raw.get("mime") not in _IMAGE_MIME:
            raise InvalidUpstreamResult("Chiyun Gemini result index is invalid")
        result = AssetRef(raw["result_id"], "reference", "active", raw["mime"], "image")
        return JobState(upstream_job_id, JobStatus.SUCCEEDED, results=(result,))

    async def acknowledge_poll_result(self, upstream_job_id: str) -> None:
        if _UPSTREAM_ID.fullmatch(upstream_job_id) is None:
            raise ValueError("Chiyun Gemini job is invalid")
        try:
            with self._locked_index():
                values = self._read_index()
                if values.pop(upstream_job_id, None) is not None:
                    self._write_index(values)
        except OSError as error:
            raise LocalRecoveryUnavailable() from error

    async def open_result(self, context: RequestContext, result_id: str, *, cookie_header: str, range_header: str | None = None, head: bool = False):
        del context, cookie_header
        path, metadata = self._root / result_id, self._root / f"{result_id}.json"
        if _RESULT_ID.fullmatch(result_id) is None or path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= _MAX_RESULT:
            return _empty_stream(404)
        mime = _detect_mime(path.read_bytes()[:16])
        if mime is None:
            return _empty_stream(404)
        del metadata
        if range_header is None:
            return _FileStream(path, mime, head=head)
        interval = _range(range_header, path.stat().st_size)
        if interval is None:
            return _empty_stream(416, size=path.stat().st_size)
        start, end = interval
        return _FileStream(path, mime, offset=start, length=end - start + 1, head=head)

    async def _post(self, model_name: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        path = f"/v1beta/models/{quote(model_name, safe='')}:generateContent"
        try:
            async with httpx.AsyncClient(base_url=self._provider.base_url, transport=self._transport, timeout=httpx.Timeout(180, connect=10), follow_redirects=False, trust_env=False) as client:
                async with client.stream("POST", path, headers={"Authorization": f"Bearer {self._api_key}"}, json=payload) as response:
                    if not 200 <= response.status_code < 300:
                        body = await response.aread()
                        raise error_from_response(httpx.Response(response.status_code, content=body[:8192]), _TEMPLATE)
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > _MAX_RESPONSE:
                            raise InvalidUpstreamResult("Chiyun Gemini response is too large")
                        raw.extend(chunk)
        except (SubmissionError, InvalidUpstreamResult):
            raise
        except httpx.HTTPError as error:
            raise error_from_transport(error, _TEMPLATE)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise InvalidUpstreamResult("Chiyun Gemini response is invalid") from error
        if not isinstance(value, Mapping):
            raise InvalidUpstreamResult("Chiyun Gemini response is invalid")
        return value

    def _read_index(self) -> dict[str, object]:
        if not self._index.exists():
            return {}
        try:
            value = json.loads(self._index.read_text(encoding="ascii"))
        except (ValueError, UnicodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @contextmanager
    def _locked_index(self):
        descriptor = os.open(
            self._index_lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.chmod(self._index_lock_path, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _write_index(self, values: Mapping[str, object]) -> None:
        temporary = self._root / f".pending.{os.getpid()}.{os.urandom(8).hex()}.tmp"
        try:
            temporary.write_text(json.dumps(values, sort_keys=True, separators=(",", ":")), encoding="ascii")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._index)
        finally:
            _safe_unlink(temporary)


def _decode_result(payload: Mapping[str, object]) -> tuple[bytes, str]:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise InvalidUpstreamResult("Chiyun Gemini candidate count is invalid")
    candidate = candidates[0]
    content = candidate.get("content") if isinstance(candidate, Mapping) else None
    parts = content.get("parts") if isinstance(content, Mapping) else None
    images: list[tuple[bytes, str]] = []
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, Mapping):
                continue
            inline = part.get("inlineData", part.get("inline_data"))
            if not isinstance(inline, Mapping):
                continue
            mime = inline.get("mimeType", inline.get("mime_type"))
            encoded = inline.get("data")
            if mime not in _IMAGE_MIME or not isinstance(encoded, str):
                raise InvalidUpstreamResult("Chiyun Gemini result is invalid")
            try:
                body = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True)
            except (binascii.Error, ValueError) as error:
                raise InvalidUpstreamResult("Chiyun Gemini result is invalid") from error
            if not 8 <= len(body) <= _MAX_RESULT or _detect_mime(body[:16]) != mime:
                raise InvalidUpstreamResult("Chiyun Gemini result is invalid")
            images.append((body, mime))
    if len(images) != 1:
        raise InvalidUpstreamResult("Chiyun Gemini result count is invalid")
    return images[0]


def _detect_mime(body: bytes) -> str | None:
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return "image/webp"
    return None

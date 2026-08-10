"""Controlled Volcano Ark image and video adapter.

The adapter is deliberately provider-facing only: browser callers see the
existing model/job/result contracts, while the API key and short-lived Ark
result URLs stay in the server's protected data directory.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import urlsplit

import httpx

from ai_creation_canvas.domain.models import AssetRef, JobRequest, JobState, JobStatus, ModelOperation, ModelSpec, RequestContext, UpstreamJob
from ai_creation_canvas.errors import ApiError, InvalidUpstreamResult, PortalUpstreamError


_ARK_URL = "https://ark.cn-beijing.volces.com"
_RESULT_ID = re.compile(r"ark_result_[0-9a-f]{64}\Z")
_MAX_RESULT_BYTES = 256 * 1024 * 1024
_IMAGE_MIME = frozenset({"image/jpeg", "image/png", "image/webp"})
_VIDEO_MIME = frozenset({"video/mp4", "video/webm"})
_MAX_CONFIG_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ArkModelDeclaration:
    model_id: str
    service_id: str
    display_name: str
    operations: tuple[ModelOperation | str, ...]
    parameter_schema: Mapping[str, object]

    def __post_init__(self) -> None:
        ModelSpec(self.model_id, self.service_id, self.display_name, self.operations, ("text",), self.parameter_schema)


class _FileStream:
    def __init__(self, path: Path, mime: str, *, offset: int = 0, length: int | None = None, head: bool = False) -> None:
        self.status_code = 200 if offset == 0 and length is None else 206
        size = path.stat().st_size
        self.headers = {"content-type": mime, "content-length": str(size if length is None else length), "accept-ranges": "bytes"}
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
            while remaining is None or remaining:
                chunk = await asyncio.to_thread(source.read, min(64 * 1024, remaining) if remaining is not None else 64 * 1024)
                if not chunk:
                    return
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk

    async def aclose(self) -> None:
        return None


class ArkGenerationAdapter:
    """Maps a data-only Ark model declaration into existing generation ports."""

    requires_portal_cookie = False

    def __init__(self, *, api_key: str, data_dir: Path | str, models: tuple[ArkModelDeclaration, ...], transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not isinstance(api_key, str) or len(api_key.strip()) < 8:
            raise ValueError("Ark API key is unavailable")
        if not models or len({model.model_id for model in models}) != len(models) or len({model.service_id for model in models}) != 1:
            raise ValueError("Ark model declarations are invalid")
        self._api_key, self._models, self._transport = api_key, {model.model_id: model for model in models}, transport
        self.service_id = models[0].service_id
        root = Path(data_dir) / "ark-results"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink():
            raise ValueError("Ark result root is unsafe")
        os.chmod(root, 0o700)
        self._root, self._index_path, self._lock = root, root / "pending.json", asyncio.Lock()

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._models))

    async def list_models(self, context: RequestContext) -> tuple[ModelSpec, ...]:
        del context
        return tuple(
            ModelSpec(model.model_id, model.service_id, model.display_name, model.operations, ("text",), model.parameter_schema)
            for model in sorted(self._models.values(), key=lambda item: item.model_id)
        )

    async def submit(self, context: RequestContext, request: JobRequest) -> UpstreamJob:
        del context
        declaration = self._models.get(request.model_id)
        if declaration is None or request.operation not in tuple(ModelOperation(value) for value in declaration.operations) or request.asset_ids:
            raise ValueError("Ark request is invalid")
        params = self._validated_params(declaration, request.params)
        if request.operation is ModelOperation.IMAGE_GENERATE:
            payload = {"model": request.model_id, "prompt": request.prompt, **params, "response_format": "url"}
            response = await self._api("POST", "/api/v3/images/generations", json=payload)
            data = self._json(response)
            items = data.get("data")
            if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], Mapping) or not isinstance(items[0].get("url"), str):
                raise InvalidUpstreamResult("Ark image response is invalid")
            upstream_id = "ark_image_" + hashlib.sha256(f"{request.model_id}\n{request.idempotency_key}".encode()).hexdigest()
            await self._record_pending(upstream_id, str(items[0]["url"]), "image")
            return UpstreamJob(self.service_id, upstream_id, JobState(upstream_id, JobStatus.QUEUED), datetime.now(UTC))
        if request.operation is ModelOperation.VIDEO_GENERATE:
            prompt = self._video_prompt(request.prompt, params)
            response = await self._api("POST", "/api/v3/contents/generations/tasks", json={"model": request.model_id, "content": [{"type": "text", "text": prompt}]})
            body = self._json(response)
            upstream_id = body.get("id")
            if not isinstance(upstream_id, str) or not upstream_id.startswith("cgt-"):
                raise InvalidUpstreamResult("Ark video response is invalid")
            return UpstreamJob(self.service_id, upstream_id, JobState(upstream_id, JobStatus.QUEUED), datetime.now(UTC))
        raise ValueError("Ark operation is unsupported")

    async def poll(self, context: RequestContext, upstream_job_id: str) -> JobState:
        del context
        pending = await self._pending(upstream_job_id)
        if pending is not None:
            result = await self._download(upstream_job_id, pending["url"], pending["kind"])
            await self._clear_pending(upstream_job_id)
            return JobState(upstream_job_id, JobStatus.SUCCEEDED, result)
        if not upstream_job_id.startswith("cgt-"):
            raise ValueError("Ark job is invalid")
        response = await self._api("GET", f"/api/v3/contents/generations/tasks/{upstream_job_id}")
        body = self._json(response)
        status = body.get("status")
        if status in {"queued", "running"}:
            return JobState(upstream_job_id, JobStatus(status))
        if status == "failed":
            return JobState(upstream_job_id, JobStatus.FAILED, error=ApiError("TASK_FAILED", "The generation task failed.", False, "ark", "generation"))
        if status != "succeeded" or not isinstance(body.get("content"), Mapping) or not isinstance(body["content"].get("video_url"), str):
            raise InvalidUpstreamResult("Ark video poll response is invalid")
        return JobState(upstream_job_id, JobStatus.SUCCEEDED, await self._download(upstream_job_id, str(body["content"]["video_url"]), "video"))

    async def open_result(self, context: RequestContext, result_id: str, *, cookie_header: str, range_header: str | None = None, head: bool = False):
        del context, cookie_header
        if not _RESULT_ID.fullmatch(result_id):
            return _missing_stream()
        media = self._root / result_id
        metadata = self._root / f"{result_id}.json"
        if not media.is_file() or not metadata.is_file():
            return _missing_stream()
        try:
            mime = json.loads(metadata.read_text(encoding="utf-8"))["mime"]
        except (OSError, ValueError, KeyError, TypeError):
            return _missing_stream()
        size = media.stat().st_size
        if range_header is None:
            return _FileStream(media, mime, head=head)
        interval = _range(range_header, size)
        if interval is None:
            return _range_missing_stream(size)
        start, end = interval
        return _FileStream(media, mime, offset=start, length=end - start + 1, head=head)

    async def _api(self, method: str, path: str, *, json: Mapping[str, object] | None = None) -> httpx.Response:
        try:
            async with httpx.AsyncClient(base_url=_ARK_URL, transport=self._transport, timeout=httpx.Timeout(30), follow_redirects=False, trust_env=False) as client:
                response = await client.request(method, path, json=json, headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"})
        except httpx.TimeoutException as error:
            raise PortalUpstreamError("UPSTREAM_TIMEOUT", retryable=True) from error
        except httpx.HTTPError as error:
            raise PortalUpstreamError("UPSTREAM_UNAVAILABLE", retryable=True) from error
        if response.status_code in {408, 429} or response.status_code >= 500:
            raise PortalUpstreamError("UPSTREAM_UNAVAILABLE", retryable=True, status_code=response.status_code)
        if response.status_code < 200 or response.status_code >= 300:
            raise PortalUpstreamError("REQUEST_REJECTED", retryable=False, status_code=response.status_code)
        return response

    @staticmethod
    def _json(response: httpx.Response) -> Mapping[str, object]:
        try:
            body = response.json()
        except ValueError as error:
            raise InvalidUpstreamResult("Ark response is not JSON") from error
        if not isinstance(body, Mapping):
            raise InvalidUpstreamResult("Ark response is not an object")
        return body

    @staticmethod
    def _validated_params(declaration: ArkModelDeclaration, values: Mapping[str, object]) -> dict[str, object]:
        schema = declaration.parameter_schema
        properties = schema.get("properties", {}) if isinstance(schema, Mapping) else {}
        if not isinstance(properties, Mapping) or set(values) - set(properties):
            raise ValueError("Ark parameters are invalid")
        result: dict[str, object] = {}
        for key, value in values.items():
            rule = properties[key]
            if not isinstance(rule, Mapping) or not isinstance(rule.get("type"), str):
                raise ValueError("Ark parameters are invalid")
            kind = rule["type"]
            if kind == "string" and not isinstance(value, str):
                raise ValueError("Ark parameters are invalid")
            if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError("Ark parameters are invalid")
            if "enum" in rule and value not in rule["enum"]:
                raise ValueError("Ark parameters are invalid")
            if isinstance(value, int) and (("minimum" in rule and value < rule["minimum"]) or ("maximum" in rule and value > rule["maximum"])):
                raise ValueError("Ark parameters are invalid")
            result[key] = value
        return result

    @staticmethod
    def _video_prompt(prompt: str, params: Mapping[str, object]) -> str:
        suffix = []
        if isinstance(params.get("ratio"), str): suffix.append(f"--ratio {params['ratio']}")
        if isinstance(params.get("duration"), int): suffix.append(f"--dur {params['duration']}")
        return " ".join((prompt, *suffix))

    async def _record_pending(self, job_id: str, url: str, kind: str) -> None:
        self._safe_result_url(url)
        async with self._lock:
            values = self._read_index(); values[job_id] = {"url": url, "kind": kind}; self._write_index(values)

    async def _pending(self, job_id: str) -> dict[str, str] | None:
        async with self._lock:
            item = self._read_index().get(job_id)
        return item if isinstance(item, dict) and isinstance(item.get("url"), str) and item.get("kind") in {"image", "video"} else None

    async def _clear_pending(self, job_id: str) -> None:
        async with self._lock:
            values = self._read_index(); values.pop(job_id, None); self._write_index(values)

    def _read_index(self) -> dict[str, dict[str, str]]:
        if not self._index_path.exists(): return {}
        try:
            value = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError): return {}
        return value if isinstance(value, dict) else {}

    def _write_index(self, values: Mapping[str, object]) -> None:
        temporary = self._index_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._index_path)

    @staticmethod
    def _safe_result_url(value: str) -> None:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".volces.com") or parsed.username or parsed.password or parsed.fragment:
            raise InvalidUpstreamResult("Ark result URL is invalid")

    async def _download(self, upstream_job_id: str, url: str, kind: str) -> AssetRef:
        self._safe_result_url(url)
        result_id = "ark_result_" + hashlib.sha256(upstream_job_id.encode()).hexdigest()
        media, metadata, temporary = self._root / result_id, self._root / f"{result_id}.json", self._root / f".{result_id}.tmp"
        if media.is_file() and metadata.is_file():
            mime = json.loads(metadata.read_text(encoding="utf-8"))["mime"]
            return AssetRef(result_id, "reference", "active", mime)
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(60), follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", url, headers={}) as response:
                    if response.status_code != 200:
                        raise PortalUpstreamError("UPSTREAM_UNAVAILABLE", retryable=response.status_code >= 500 or response.status_code in {408, 429}, status_code=response.status_code)
                    mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if mime not in (_IMAGE_MIME if kind == "image" else _VIDEO_MIME):
                        raise InvalidUpstreamResult("Ark result MIME is invalid")
                    total = 0
                    with temporary.open("wb") as destination:
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > _MAX_RESULT_BYTES:
                                raise InvalidUpstreamResult("Ark result is too large")
                            destination.write(chunk)
            os.chmod(temporary, 0o600); os.replace(temporary, media)
            metadata.write_text(json.dumps({"mime": mime}), encoding="utf-8"); os.chmod(metadata, 0o600)
            return AssetRef(result_id, "reference", "active", mime)
        finally:
            if temporary.exists(): temporary.unlink()


def _range(value: str, size: int) -> tuple[int, int] | None:
    if not value.startswith("bytes=") or "," in value: return None
    left, separator, right = value[6:].partition("-")
    if not separator or (not left and not right) or (left and not left.isdecimal()) or (right and not right.isdecimal()): return None
    if not left:
        suffix = int(right); return None if suffix <= 0 else (max(0, size - suffix), size - 1)
    start = int(left)
    if start >= size: return None
    end = min(int(right), size - 1) if right else size - 1
    return None if end < start else (start, end)


def _missing_stream():
    stream = _FileStream.__new__(_FileStream); stream.status_code, stream.headers = 404, {"content-length": "0"}; stream.aiter_bytes = _empty  # type: ignore[attr-defined]
    return stream


def _range_missing_stream(size: int):
    stream = _FileStream.__new__(_FileStream); stream.status_code, stream.headers = 416, {"content-length": "0", "content-range": f"bytes */{size}"}; stream.aiter_bytes = _empty  # type: ignore[attr-defined]
    return stream


async def _empty():
    if False:
        yield b""


def load_ark_model_declarations(path: Path | str, root: Path | str) -> tuple[ArkModelDeclaration, ...]:
    """Load only bounded data declarations from an administrator-owned file."""
    trusted_root = Path(root).resolve(strict=False)
    candidate = Path(path)
    try:
        if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > _MAX_CONFIG_BYTES:
            raise ValueError
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(trusted_root)
        body = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(body, Mapping) or set(body) != {"models"} or not isinstance(body["models"], list) or not body["models"]:
            raise ValueError
        declarations = []
        for item in body["models"]:
            if not isinstance(item, Mapping) or set(item) != {"model_id", "service_id", "display_name", "operations", "parameter_schema"}:
                raise ValueError
            model_id, service_id, display_name, operations, parameter_schema = (item["model_id"], item["service_id"], item["display_name"], item["operations"], item["parameter_schema"])
            if not all(isinstance(value, str) and 1 <= len(value) <= 128 for value in (model_id, service_id, display_name)):
                raise ValueError
            if not model_id.isascii() or not service_id.isascii() or not model_id.replace("-", "").replace("_", "").isalnum() or not service_id.replace("-", "").replace("_", "").isalnum():
                raise ValueError
            if not isinstance(operations, list) or not operations or any(value not in {"image.generate", "video.generate"} for value in operations):
                raise ValueError
            if not isinstance(parameter_schema, Mapping) or parameter_schema.get("type") != "object" or parameter_schema.get("additionalProperties") is not False or not isinstance(parameter_schema.get("properties"), Mapping):
                raise ValueError
            if len(parameter_schema["properties"]) > 16 or any(not isinstance(key, str) or not isinstance(rule, Mapping) or rule.get("type") not in {"string", "integer"} for key, rule in parameter_schema["properties"].items()):
                raise ValueError
            declarations.append(ArkModelDeclaration(model_id, service_id, display_name, tuple(operations), parameter_schema))
        if len({item.model_id for item in declarations}) != len(declarations):
            raise ValueError
        return tuple(declarations)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Ark model configuration is invalid") from error


def build_ark_adapters(*, api_key: str, data_dir: Path | str, config_path: Path | str, config_root: Path | str, transport: httpx.AsyncBaseTransport | None = None) -> tuple[ArkGenerationAdapter, ...]:
    grouped: dict[str, list[ArkModelDeclaration]] = {}
    for declaration in load_ark_model_declarations(config_path, config_root):
        grouped.setdefault(declaration.service_id, []).append(declaration)
    return tuple(
        ArkGenerationAdapter(api_key=api_key, data_dir=data_dir, models=tuple(items), transport=transport)
        for _, items in sorted(grouped.items())
    )

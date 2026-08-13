"""One-shot, API-level paid acceptance runner. Never import this from the server."""

from __future__ import annotations

from http.cookiejar import CookieJar
import binascii
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import stat
import zlib
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from typing import Callable, NamedTuple

from ai_creation_canvas.acceptance_models import acceptance_model_profiles


_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
_child_process: subprocess.Popen[bytes] | None = None


class OwnedFile(NamedTuple):
    parent: Path
    name: str
    parent_device: int
    parent_inode: int
    file_device: int
    file_inode: int


_owned_pool_file: OwnedFile | None = None


_CHANNEL_MODELS = {
    "banana-chiyun": "banana",
    "banana-t8star": "banana",
    "gpt-image2-chiyun": "gpt-image2",
    "seedream-ark": "seedream",
    "seedance-ark": "seedance",
}

_FROZEN_PROFILES = acceptance_model_profiles()["profiles"]
_CHANNEL_DEFINITIONS = {
    "banana-chiyun": ("banana", "chiyun", _FROZEN_PROFILES["banana"]["provider_model_name"], _FROZEN_PROFILES["banana"]["adapter_type"], _FROZEN_PROFILES["banana"]["family"], _FROZEN_PROFILES["banana"]["contract"]),
    "banana-t8star": ("banana", "t8star", _FROZEN_PROFILES["banana"]["provider_model_name"], _FROZEN_PROFILES["banana"]["adapter_type"], _FROZEN_PROFILES["banana"]["family"], _FROZEN_PROFILES["banana"]["contract"]),
    "gpt-image2-chiyun": ("gpt-image2", "chiyun", _FROZEN_PROFILES["gpt-image2"]["provider_model_name"], _FROZEN_PROFILES["gpt-image2"]["adapter_type"], _FROZEN_PROFILES["gpt-image2"]["family"], _FROZEN_PROFILES["gpt-image2"]["contract"]),
    "seedream-ark": ("seedream", "ark", _FROZEN_PROFILES["seedream"]["provider_model_name"], _FROZEN_PROFILES["seedream"]["adapter_type"], _FROZEN_PROFILES["seedream"]["family"], _FROZEN_PROFILES["seedream"]["contract"]),
    "seedance-ark": ("seedance", "ark", _FROZEN_PROFILES["seedance"]["provider_model_name"], _FROZEN_PROFILES["seedance"]["adapter_type"], _FROZEN_PROFILES["seedance"]["family"], _FROZEN_PROFILES["seedance"]["contract"]),
}


class PaidCall(NamedTuple):
    phase: str
    channel_id: str | None
    model_id: str
    sample_index: int | None = None


def paid_call_plan(
    channel_ids: tuple[str, ...],
    *,
    banana_sample_count: int,
    maximum_paid_calls: int,
) -> tuple[PaidCall, ...]:
    if (
        not channel_ids
        or len(channel_ids) != len(set(channel_ids))
        or any(channel not in _CHANNEL_MODELS for channel in channel_ids)
    ):
        raise ValueError("paid channel allowlist is invalid")
    if type(maximum_paid_calls) is not int or not 1 <= maximum_paid_calls <= 20:
        raise ValueError("paid call budget must be between one and twenty")
    if type(banana_sample_count) is not int or not 0 <= banana_sample_count <= 20:
        raise ValueError("Banana sample count is invalid")
    if banana_sample_count and not any(_CHANNEL_MODELS[channel] == "banana" for channel in channel_ids):
        raise ValueError("Banana sample requires a selected Banana channel")
    calls = tuple(PaidCall("smoke", channel, _CHANNEL_MODELS[channel]) for channel in channel_ids) + tuple(
        PaidCall("banana_sample", None, "banana", index + 1)
        for index in range(banana_sample_count)
    )
    if len(calls) > maximum_paid_calls:
        raise ValueError("paid call plan exceeds the explicit budget")
    return calls


def execute_paid_plan(
    plan: tuple[PaidCall, ...],
    *,
    activate_channel: Callable[[str], None],
    activate_banana: Callable[[], None],
    execute: Callable[[PaidCall], dict[str, object]],
    recorder: "PaidRunRecorder",
) -> tuple[dict[str, object], ...]:
    records: list[dict[str, object]] = []
    batch_enabled = False
    for call in plan:
        recorder.begin_call(call)
        try:
            if call.phase == "smoke":
                if call.channel_id is None:
                    raise RuntimeError("paid smoke channel is missing")
                activate_channel(call.channel_id)
            elif call.phase == "banana_sample":
                if not batch_enabled:
                    activate_banana()
                    batch_enabled = True
            else:
                raise RuntimeError("paid call phase is invalid")
        except Exception as error:
            recorder.fail_current(error, failure_class="activation_failed")
            raise
        recorder.mark_attempted()
        try:
            record = execute(call)
        except Exception as error:
            recorder.fail_current(error)
            raise
        recorder.record_current(record)
        records.append(record)
        if record.get("status") != "succeeded":
            raise RuntimeError("paid acceptance call failed")
    return tuple(records)


def acceptance_definitions(
    channel_ids: tuple[str, ...],
    *,
    chiyun_origin: str,
    t8star_origin: str,
) -> dict[str, list[dict[str, object]]]:
    del chiyun_origin, t8star_origin
    paid_call_plan(channel_ids, banana_sample_count=0, maximum_paid_calls=len(channel_ids))
    origins = {
        "chiyun": None,
        "t8star": None,
        "ark": "https://ark.cn-beijing.volces.com",
    }
    providers: list[dict[str, object]] = []
    models: list[dict[str, object]] = []
    routes: list[dict[str, object]] = []
    seen_providers: set[str] = set()
    seen_models: set[str] = set()
    active_models: set[str] = set()
    for priority, channel_id in enumerate(channel_ids, start=1):
        model_id, provider_id, provider_model_name, adapter_type, family, contract = _CHANNEL_DEFINITIONS[channel_id]
        if provider_id not in seen_providers:
            origin = origins[provider_id]
            if not origin:
                raise ValueError(f"{channel_id} has no code-approved origin")
            providers.append({
                "provider_id": provider_id,
                "display_name": f"Paid acceptance {provider_id}",
                "adapter_type": adapter_type,
                "base_url": origin,
                "credential_ref": "acceptance-only",
                "enabled": True,
            })
            seen_providers.add(provider_id)
        if model_id not in seen_models:
            models.append({
                "model_id": model_id,
                "display_name": model_id,
                "introduction": "Bounded paid acceptance model.",
                "modality": str(contract["output_media_type"]),
                "operation_contracts": [json.loads(json.dumps(contract))],
                "enabled": True,
            })
            seen_models.add(model_id)
        routes.append({
            "route_id": channel_id,
            "model_id": model_id,
            "provider_id": provider_id,
            "provider_model_name": provider_model_name,
            "adapter_type": adapter_type,
            "credential_pool_ref": f"paid-{channel_id}",
            "family": family,
            "operation_contracts": [json.loads(json.dumps(contract))],
            "priority": priority,
            "max_concurrency": 1,
            "enabled": model_id not in active_models,
        })
        active_models.add(model_id)
    return {"providers": providers, "models": models, "routes": routes}


def request_for_paid_call(call: PaidCall, owned_asset_id: str) -> dict[str, object]:
    if not isinstance(call, PaidCall) or call.model_id not in set(_CHANNEL_MODELS.values()):
        raise ValueError("paid call is invalid")
    common: dict[str, object] = {
        "model_id": call.model_id,
        "asset_ids": [],
        "idempotency_key": secrets.token_urlsafe(24),
    }
    if call.model_id in {"banana", "gpt-image2"}:
        return {
            **common,
            "operation": "image.edit",
            "prompt": "Keep the green circle and place it on a clean black background.",
            "params": {"size": "1024x1024", "output_count": 1},
            "inputs": {"reference_images": [owned_asset_id]},
        }
    if call.model_id == "seedream":
        return {
            **common,
            "operation": "image.edit",
            "prompt": "Keep the green circle and place it on a clean black background.",
            "params": {"size": "1K", "watermark": False, "output_format": "png", "prompt_optimization": "fast"},
            "inputs": {"reference_images": [owned_asset_id]},
        }
    return {
        **common,
        "operation": "video.generate",
        "prompt": "A green circle moves slowly across a black background.",
        "params": {"ratio": "16:9", "resolution": "480p", "duration": 5, "generate_audio": False, "watermark": False},
        "inputs": {},
    }


def write_credential_pool_config(
    path: Path,
    channel_ids: tuple[str, ...],
    keys: dict[str, str],
) -> OwnedFile:
    paid_call_plan(channel_ids, banana_sample_count=0, maximum_paid_calls=len(channel_ids))
    key_names = {
        "banana-chiyun": "CHIYUN_API_KEY",
        "banana-t8star": "T8STAR_API_KEY",
        "gpt-image2-chiyun": "CHIYUN_API_KEY",
        "seedream-ark": "ARK_API_KEY",
        "seedance-ark": "ARK_API_KEY",
    }
    groups = {"chiyun": "chiyun", "t8star": "gemini", "ark": "official"}
    selected = {key_names[channel] for channel in channel_ids}
    if (
        not selected <= set(keys)
        or any(
            not isinstance(keys[name], str)
            or not 8 <= len(keys[name]) <= 4096
            or any(char in keys[name] for char in "\r\n\0")
            for name in selected
        )
    ):
        raise RuntimeError("selected paid credential is unavailable")
    pools: dict[str, object] = {}
    for channel in channel_ids:
        _model_id, provider_id, _provider_model, _adapter, family, _contract = _CHANNEL_DEFINITIONS[channel]
        pools[f"paid-{channel}"] = {
            "provider": provider_id,
            "group": groups[provider_id],
            "allowed_families": [family],
            "keys": [{"id": "acceptance-credential", "api_key": keys[key_names[channel]], "max_concurrency": 1}],
        }
    candidate = Path(path)
    if candidate.exists() or candidate.is_symlink() or not candidate.parent.is_dir() or candidate.parent.is_symlink():
        raise RuntimeError("acceptance credential pool path is unsafe")
    temporary = candidate.with_name(f".{candidate.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump({"version": 1, "pools": pools}, handle, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, candidate)
        os.chmod(candidate, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    parent_details = candidate.parent.lstat()
    file_details = candidate.lstat()
    if (
        not stat.S_ISDIR(parent_details.st_mode)
        or stat.S_ISLNK(parent_details.st_mode)
        or not stat.S_ISREG(file_details.st_mode)
        or file_details.st_mode & 0o077
    ):
        raise RuntimeError("acceptance credential pool ownership is unsafe")
    return OwnedFile(
        candidate.parent,
        candidate.name,
        parent_details.st_dev,
        parent_details.st_ino,
        file_details.st_dev,
        file_details.st_ino,
    )


def _remove_owned_file(owned: OwnedFile | None) -> None:
    if not isinstance(owned, OwnedFile):
        return
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(owned.parent, flags)
        try:
            parent_details = os.fstat(descriptor)
            file_details = os.stat(owned.name, dir_fd=descriptor, follow_symlinks=False)
            if (
                not stat.S_ISDIR(parent_details.st_mode)
                or (parent_details.st_dev, parent_details.st_ino) != (owned.parent_device, owned.parent_inode)
                or not stat.S_ISREG(file_details.st_mode)
                or (file_details.st_dev, file_details.st_ino) != (owned.file_device, owned.file_inode)
            ):
                return
            os.unlink(owned.name, dir_fd=descriptor)
        finally:
            os.close(descriptor)
    except (FileNotFoundError, NotADirectoryError, OSError):
        return


def create_acceptance_app(
    *,
    data_dir: Path,
    static_dir: Path,
    port: int,
    credential_pool_path: Path,
    channel_ids: tuple[str, ...],
    chiyun_origin: str,
    t8star_origin: str,
    maximum_provider_submissions: int,
):
    from ai_creation_canvas.catalog import ProviderSubmissionBudget
    from ai_creation_canvas.app import create_app
    from ai_creation_canvas.config import Settings
    from ai_creation_canvas.domain.models import ModelInputPort
    from ai_creation_canvas.model_registry import OperationContract, ProviderDefinition
    from ai_creation_canvas.model_routing import LogicalModelDefinition, ModelRouteDefinition
    from ai_creation_canvas.storage.sqlite import CanvasStore

    definitions = acceptance_definitions(
        channel_ids,
        chiyun_origin=chiyun_origin,
        t8star_origin=t8star_origin,
    )
    store = CanvasStore(data_dir)

    def contract(value: dict[str, object]) -> OperationContract:
        raw_ports = value["input_ports"]
        assert isinstance(raw_ports, list)
        ports = tuple(
            ModelInputPort(
                str(item["port_id"]), str(item["media_type"]), int(item["min_items"]), int(item["max_items"]),
            )
            for item in raw_ports
            if isinstance(item, dict)
        )
        schema = value["parameter_schema"]
        mappings = value["parameter_mappings"]
        assert isinstance(schema, dict) and isinstance(mappings, dict)
        return OperationContract(str(value["operation"]), ports, str(value["output_media_type"]), schema, mappings)

    for item in definitions["providers"]:
        store.create_provider_definition(ProviderDefinition(**item), actor_user_id="paid-acceptance")
    for item in definitions["models"]:
        raw_contracts = item["operation_contracts"]
        assert isinstance(raw_contracts, list)
        store.create_logical_model(LogicalModelDefinition(
            str(item["model_id"]), str(item["display_name"]), str(item["introduction"]), str(item["modality"]),
            tuple(contract(value) for value in raw_contracts if isinstance(value, dict)), bool(item["enabled"]),
        ), actor_user_id="paid-acceptance")
    for item in definitions["routes"]:
        raw_contracts = item["operation_contracts"]
        assert isinstance(raw_contracts, list)
        store.create_model_route(ModelRouteDefinition(
            str(item["route_id"]), str(item["model_id"]), str(item["provider_id"]), str(item["provider_model_name"]),
            str(item["adapter_type"]), str(item["credential_pool_ref"]), str(item["family"]),
            tuple(contract(value) for value in raw_contracts if isinstance(value, dict)),
            int(item["priority"]), int(item["max_concurrency"]), bool(item["enabled"]),
        ), actor_user_id="paid-acceptance")
    origin = f"http://127.0.0.1:{port}"
    app = create_app(
        Settings(
            environment="development",
            port=port,
            data_dir=data_dir,
            portal_internal_token="paid-acceptance-local-secret",
            identity_mode="local",
            allowed_origins=(origin,),
            credential_pools_path=credential_pool_path,
            credential_pools_root=credential_pool_path.parent,
        ),
        static_dir=static_dir,
        canvas_store=store,
        provider_submission_budget=ProviderSubmissionBudget(maximum_provider_submissions),
    )
    runtime = app.state.managed_routing_runtime
    if runtime is None:
        raise RuntimeError("paid managed routing runtime is unavailable")
    for route in store.list_model_routes(include_archived=False):
        runtime.adapter_factory.validate_route(route)
    accounts = app.state.local_auth.bootstrap_accounts(())
    return app, accounts


def _stop_child() -> None:
    global _child_process
    process, _child_process = _child_process, None
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait(timeout=2)


def _remove_credential_file() -> None:
    """Drop inherited locators without deleting caller-owned paths."""
    global _owned_pool_file
    owned, _owned_pool_file = _owned_pool_file, None
    os.environ.pop("AICC_ACCEPTANCE_KEY_FILE", None)
    os.environ.pop("AICC_ACCEPTANCE_POOL_FILE", None)
    _remove_owned_file(owned)


def _signal_cleanup(signum: int, _frame: object) -> None:
    _remove_credential_file()
    _stop_child()
    raise SystemExit(128 + signum)


def _install_signal_cleanup() -> None:
    for item in _SIGNALS:
        signal.signal(item, _signal_cleanup)


def _spawn_child(arguments: list[str], *, cwd: Path, environment: dict[str, str], stdout: object, stderr: object) -> subprocess.Popen[bytes]:
    global _child_process
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, _SIGNALS)
    try:
        _child_process = subprocess.Popen(arguments, cwd=cwd, env=environment, stdout=stdout, stderr=stderr, preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, previous))
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)
    return _child_process


def paid_image_request(image_model: str, owned_asset_id: str) -> dict[str, object]:
    return {"operation": "image.edit", "model_id": image_model, "prompt": "Keep the green circle and place it on a clean black background.", "params": {"size": "1024x1024"}, "asset_ids": [], "inputs": {"reference_images": [owned_asset_id]}, "idempotency_key": secrets.token_urlsafe(24)}


def paid_video_request(video_model: str, image_result_asset_id: str) -> dict[str, object]:
    return {"operation": "video.generate", "model_id": video_model, "prompt": "The green circle moves slowly across the black background.", "params": {"ratio": "16:9", "resolution": "480p", "duration": 5, "generate_audio": False, "watermark": False, "return_last_frame": False}, "asset_ids": [], "inputs": {"reference_images": [image_result_asset_id]}, "idempotency_key": secrets.token_urlsafe(24)}


def paid_job_requests(image_model: str, video_model: str, owned_asset_id: str, image_result_asset_id: str) -> tuple[dict[str, object], dict[str, object]]:
    return paid_image_request(image_model, owned_asset_id), paid_video_request(video_model, image_result_asset_id)


def sanitized_result_record(
    *,
    phase: str,
    logical_model: str,
    selected_channel: str,
    status: str,
    mime: str,
    byte_count: int,
    duration_seconds: float,
    user_id: str,
) -> dict[str, object]:
    return {
        "phase": phase,
        "logical_model": logical_model,
        "selected_channel": selected_channel,
        "status": status,
        "mime": mime,
        "bytes": byte_count,
        "duration_seconds": round(duration_seconds, 2),
        "user_id": user_id,
    }


def render_record(record: dict[str, object]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def reference_png() -> bytes:
    """Return a tiny valid 64x64 RGB PNG above the provider's 14px floor."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\x00\xff\x55" * 64
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(row * 64, 9)) + chunk(b"IEND", b"")


def verify_media_file(path: Path, mime: str, kind: str) -> dict[str, object]:
    allowed = {
        "image": {"image/png", "image/jpeg", "image/webp"},
        "video": {"video/mp4", "video/webm", "video/quicktime"},
    }
    candidate = Path(path)
    if kind not in allowed or mime not in allowed[kind] or candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size < 8:
        raise RuntimeError("paid result media contract failed")
    executable = shutil.which("ffprobe")
    if executable is None:
        raise RuntimeError("paid result decoder is unavailable")
    result = subprocess.run(
        [
            executable,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type,width,height:format=duration",
            "-of", "json",
            str(candidate),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    try:
        payload = json.loads(result.stdout) if result.returncode == 0 else {}
        streams = payload["streams"]
        stream = streams[0]
        projection = {
            "codec_type": stream["codec_type"],
            "width": int(stream["width"]),
            "height": int(stream["height"]),
        }
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("paid result decode failed") from None
    if projection["codec_type"] != "video" or projection["width"] <= 0 or projection["height"] <= 0:
        raise RuntimeError("paid result decode failed")
    if kind == "video":
        try:
            duration = float(payload["format"]["duration"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("paid result decode failed") from None
        if duration <= 0:
            raise RuntimeError("paid result decode failed")
    return projection


def consume_server_keys() -> dict[str, str]:
    allowed = {"ARK_API_KEY", "CHIYUN_API_KEY", "T8STAR_API_KEY"}
    if any(name in os.environ for name in allowed):
        raise RuntimeError("paid credential leaked into acceptance environment")
    path_text = os.environ.pop("AICC_ACCEPTANCE_KEY_FILE", "")
    path = Path(path_text)
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or details.st_mode & 0o077 or not 1 <= details.st_size <= 16 * 1024:
        raise RuntimeError("unsafe acceptance credential file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        current = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_mode & 0o077
            or (details.st_dev, details.st_ino) != (opened.st_dev, opened.st_ino)
            or (details.st_dev, details.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise RuntimeError("unsafe acceptance credential file")
        raw = handle.read(16 * 1024 + 1)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, ValueError, UnboundLocalError):
        raise RuntimeError("invalid acceptance credential bundle") from None
    if (
        not isinstance(payload, dict)
        or not payload
        or not set(payload) <= allowed
        or any(
            not isinstance(value, str)
            or not 8 <= len(value) <= 4096
            or any(char in value for char in "\r\n\0")
            for value in payload.values()
        )
    ):
        raise RuntimeError("invalid acceptance credential bundle")
    return dict(payload)


def consume_server_key() -> str:
    """Compatibility wrapper for the pre-routing Ark-only probe."""
    values = consume_server_keys()
    if set(values) != {"ARK_API_KEY"}:
        raise RuntimeError("invalid Ark-only acceptance credential")
    return values["ARK_API_KEY"]


def server_environment(api_key: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("AICC_ACCEPTANCE_KEY_FILE", None)
    environment["ARK_API_KEY"] = api_key
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


class PaidDownload(NamedTuple):
    job_id: str
    result_asset_id: str
    result_url: str
    mime: str
    byte_count: int
    duration_seconds: float


class ApiStatusError(RuntimeError):
    def __init__(self, status: int) -> None:
        super().__init__("acceptance API request failed")
        self.status = status


class PaidAcceptanceFailure(RuntimeError):
    def __init__(self, failure_class: str) -> None:
        allowed = {"submission_unknown", "business_4xx", "retryable_http", "service_5xx", "transport", "timeout", "generation_failed", "acceptance_contract"}
        if failure_class not in allowed:
            raise ValueError("paid acceptance failure class is invalid")
        super().__init__("paid acceptance failed")
        self.failure_class = failure_class


class ApiSession:
    def __init__(self, origin: str) -> None:
        self.origin = origin
        self.csrf = ""
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def request(self, method: str, path: str, payload: object | None = None, extra_headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        if not path.startswith("/api/v1/") or path.startswith("//"):
            raise RuntimeError("unsafe same-origin path")
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        headers = {"Accept": "application/json", "Origin": self.origin}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if method not in {"GET", "HEAD", "OPTIONS"} and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        headers.update(extra_headers or {})
        request = Request(self.origin + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=60) as response:
                return response.status, {key.lower(): value for key, value in response.headers.items()}, response.read()
        except HTTPError as error:
            return error.code, {key.lower(): value for key, value in error.headers.items()}, error.read()

    def json(self, method: str, path: str, payload: object | None = None, expected: tuple[int, ...] = (200,)) -> dict[str, object]:
        status, _headers, body = self.request(method, path, payload)
        if status not in expected:
            raise ApiStatusError(status)
        value = json.loads(body) if body else {}
        if not isinstance(value, dict):
            raise RuntimeError("invalid api response")
        return value

    def authenticate(self, username: str, initial_password: str) -> dict[str, object]:
        login = self.json("POST", "/api/v1/auth/login", {"username": username, "password": initial_password})
        self.csrf = str(login["csrf_token"])
        password = secrets.token_urlsafe(24)
        changed = self.json("POST", "/api/v1/auth/change-password", {"current_password": initial_password, "new_password": password})
        self.csrf = str(changed["csrf_token"])
        return dict(changed["user"])

    def upload_reference_png(self) -> str:
        image = reference_png()
        boundary = "----aicc-paid-acceptance-boundary"
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\nreference\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"media_type\"\r\n\r\nimage\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"reference.png\"\r\nContent-Type: image/png\r\n\r\n".encode() + image + b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        body = b"".join(parts)
        headers = {"Accept": "application/json", "Origin": self.origin, "X-CSRF-Token": self.csrf, "Content-Type": f"multipart/form-data; boundary={boundary}"}
        request = Request(self.origin + "/api/v1/assets", data=body, headers=headers, method="POST")
        with self.opener.open(request, timeout=60) as response:
            if response.status != 201:
                raise RuntimeError("asset upload failed")
            payload = json.loads(response.read())
        asset_id = payload.get("asset_id") if isinstance(payload, dict) else None
        if not isinstance(asset_id, str):
            raise RuntimeError("asset upload contract missing")
        return asset_id

    def download(self, path: str) -> tuple[str, int]:
        if not path.startswith("/api/v1/results/") or path.startswith("//"):
            raise RuntimeError("unsafe result path")
        request = Request(self.origin + path, headers={"Accept": "*/*", "Origin": self.origin}, method="GET")
        temporary_path: Path | None = None
        try:
            with self.opener.open(request, timeout=180) as response:
                mime = response.headers.get_content_type()
                suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}.get(mime, ".media")
                with tempfile.NamedTemporaryFile(prefix="aicc-paid-result.", suffix=suffix, delete=False) as output:
                    temporary_path = Path(output.name)
                    os.chmod(temporary_path, 0o600)
                    total = 0
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > 256 * 1024 * 1024:
                            raise RuntimeError("paid result exceeded the acceptance limit")
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            kind = "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else ""
            verify_media_file(temporary_path, mime, kind)
            return mime, total
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def _project_document(asset_id: str, image_model: str, video_model: str, asset_bytes: int) -> dict[str, object]:
    now = "2026-08-11T00:00:00.000Z"
    nodes = [
        {"id": "prompt", "type": "text", "title": "Prompt", "position": {"x": 40, "y": 40}, "width": 300, "height": 180, "metadata": {"graph": {"schemaVersion": 1, "role": "prompt", "text": "Paid acceptance prompt", "outputPortId": "prompt"}}},
        {"id": "reference", "type": "image", "title": "Reference", "position": {"x": 40, "y": 280}, "width": 320, "height": 240, "metadata": {"graph": {"schemaVersion": 1, "role": "media-collection", "mediaType": "image", "outputPortId": "media", "items": [{"id": "owned-item", "assetId": asset_id, "displayName": "reference.png", "mimeType": "image/png", "bytes": asset_bytes, "width": 64, "height": 64}]}}},
        {"id": "image-model", "type": "config", "title": "Seedream edit", "position": {"x": 440, "y": 80}, "width": 340, "height": 360, "metadata": {"graph": {"schemaVersion": 1, "role": "model", "modelId": image_model, "operation": "image.edit", "inputPorts": [{"id": "prompt", "accepts": "prompt"}, {"id": "reference_images", "accepts": "image"}], "outputPortId": "result", "parameters": {"size": "1024x1024"}}}},
        {"id": "video-model", "type": "config", "title": "Seedance video", "position": {"x": 860, "y": 80}, "width": 340, "height": 420, "metadata": {"graph": {"schemaVersion": 1, "role": "model", "modelId": video_model, "operation": "video.generate", "inputPorts": [{"id": "prompt", "accepts": "prompt"}, {"id": "reference_images", "accepts": "image"}], "outputPortId": "result", "parameters": {"ratio": "16:9", "resolution": "480p", "duration": 5, "generate_audio": False, "watermark": False, "return_last_frame": False}}}},
    ]
    connections = [
        {"id": "prompt-image", "fromNodeId": "prompt", "fromPortId": "prompt", "toNodeId": "image-model", "toPortId": "prompt"},
        {"id": "reference-image", "fromNodeId": "reference", "fromPortId": "media", "toNodeId": "image-model", "toPortId": "reference_images"},
        {"id": "prompt-video", "fromNodeId": "prompt", "fromPortId": "prompt", "toNodeId": "video-model", "toPortId": "prompt"},
    ]
    return {"id": "paid-acceptance-canvas", "title": "Paid acceptance", "createdAt": now, "updatedAt": now, "nodes": nodes, "connections": connections, "chatSessions": [], "activeChatId": None, "backgroundMode": "lines", "showImageInfo": False, "viewport": {"x": 0, "y": 0, "k": 1}, "graphSchemaVersion": 1}


def _poll_and_download(user: ApiSession, other: ApiSession, payload: dict[str, object], kind: str) -> PaidDownload:
    started = time.monotonic()
    created = user.json("POST", "/api/v1/jobs", payload, expected=(201,))
    job_id = str(created["id"])
    if other.request("GET", f"/api/v1/jobs/{job_id}")[0] != 404:
        raise RuntimeError("owner isolation failed")
    state = created
    deadline = time.monotonic() + 20 * 60
    while state.get("status") in {"uploading", "submitting", "queued", "running"} and time.monotonic() < deadline:
        time.sleep(2)
        state = user.json("GET", f"/api/v1/jobs/{job_id}")
    if state.get("status") != "succeeded":
        status = str(state.get("status", ""))
        failure_class = "submission_unknown" if status == "submission_unknown" else "timeout" if status in {"uploading", "submitting", "queued", "running"} else "generation_failed"
        raise PaidAcceptanceFailure(failure_class)
    results = state.get("results")
    if not isinstance(results, list) or len(results) < 1 or not isinstance(results[0], dict):
        raise RuntimeError("result contract missing")
    if results[0].get("media_type") != kind:
        raise RuntimeError("result media type contract failed")
    result_url, result_asset_id = str(results[0].get("url", "")), str(results[0].get("asset_id", ""))
    if other.request("GET", result_url)[0] != 404:
        raise RuntimeError("result owner isolation failed")
    head_status, head_headers, _ = user.request("HEAD", result_url)
    range_status, range_headers, range_body = user.request("GET", result_url, extra_headers={"Range": "bytes=0-1023"})
    if head_status != 200 or range_status != 206 or not range_body or "content-range" not in range_headers:
        raise RuntimeError("result streaming contract failed")
    mime, byte_count = user.download(result_url)
    allowed_mime = {"image": {"image/png", "image/jpeg", "image/webp"}, "video": {"video/mp4", "video/webm", "video/quicktime"}}
    if kind not in allowed_mime or mime not in allowed_mime[kind] or head_headers.get("content-type", "").split(";", 1)[0] != mime:
        raise RuntimeError("result MIME contract failed")
    replayed = user.json("POST", "/api/v1/jobs", payload, expected=(201,))
    if str(replayed.get("id", "")) != job_id or replayed.get("status") != "succeeded":
        raise RuntimeError("job idempotency contract failed")
    return PaidDownload(job_id, result_asset_id, result_url, mime, byte_count, time.monotonic() - started)


def _credentials(log_path: Path, process: subprocess.Popen[bytes]) -> tuple[str, str]:
    pattern = re.compile(r"^(canvas-(?:admin|user)): ([A-Za-z0-9_-]{12,128})$", re.MULTILINE)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and process.poll() is None:
        text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        found = dict(pattern.findall(text))
        if set(found) == {"canvas-admin", "canvas-user"}:
            return found["canvas-admin"], found["canvas-user"]
        time.sleep(0.1)
    raise RuntimeError("isolated accounts were not created")


def run_paid_graph(user: ApiSession, admin: ApiSession, model_ids: list[str], emit: Callable[[str], None]) -> None:
    owned_asset_id = user.upload_reference_png()
    if admin.request("GET", f"/api/v1/assets/{owned_asset_id}")[0] not in {403, 404}:
        raise RuntimeError("asset owner isolation failed")
    project = _project_document(owned_asset_id, *model_ids, len(reference_png()))
    persisted = user.json("POST", "/api/v1/projects", project, expected=(201,))
    saved = persisted.get("project")
    if not isinstance(saved, dict) or saved.get("nodes") != project["nodes"] or saved.get("connections") != project["connections"]:
        raise RuntimeError("canvas graph persistence failed")
    if admin.request("GET", "/api/v1/projects/paid-acceptance-canvas")[0] != 404:
        raise RuntimeError("project owner isolation failed")
    image_request = paid_image_request(model_ids[0], owned_asset_id)
    image_record, image_result_asset_id, _image_result_url = _poll_and_download(user, admin, image_request, "image")
    emit(render_record(image_record))
    video_request = paid_video_request(model_ids[1], image_result_asset_id)
    video_record, _video_result_asset_id, _video_result_url = _poll_and_download(user, admin, video_request, "video")
    emit(render_record(video_record))


def _set_model_routes(admin: ApiSession, model_id: str, enabled_route_ids: set[str]) -> None:
    payload = admin.json("GET", f"/api/v1/admin/logical-models/{model_id}/routes")
    routes = payload.get("routes")
    if not isinstance(routes, list):
        raise RuntimeError("acceptance route list is invalid")
    observed = {str(item.get("route_id")) for item in routes if isinstance(item, dict)}
    if not enabled_route_ids <= observed:
        raise RuntimeError("acceptance route is unavailable")
    for raw in routes:
        if not isinstance(raw, dict):
            raise RuntimeError("acceptance route is invalid")
        route_id = str(raw.get("route_id", ""))
        want = route_id in enabled_route_ids
        if bool(raw.get("enabled")) == want:
            continue
        revision = raw.get("revision")
        if type(revision) is not int:
            raise RuntimeError("acceptance route revision is invalid")
        action = "enable" if want else "disable"
        updated = admin.json(
            "POST",
            f"/api/v1/admin/logical-models/{model_id}/routes/{route_id}/{action}",
            {"revision": revision},
        )
        if updated.get("enabled") is not want:
            raise RuntimeError("acceptance route lifecycle failed")


def _failure_class(error: Exception) -> str:
    if isinstance(error, PaidAcceptanceFailure):
        return error.failure_class
    if isinstance(error, ApiStatusError):
        if error.status in {408, 429}:
            return "retryable_http"
        if 400 <= error.status < 500:
            return "business_4xx"
        if error.status >= 500:
            return "service_5xx"
    if isinstance(error, (URLError, TimeoutError)):
        return "transport"
    return "acceptance_contract"


_SAFE_RUN_FAILURE_CLASSES = frozenset({
    "submission_unknown", "business_4xx", "retryable_http", "service_5xx",
    "transport", "timeout", "generation_failed", "acceptance_contract",
    "upload_failed", "preflight_failed", "activation_failed", "blocked_after_failure",
})


def _failure_record(
    call: PaidCall,
    *,
    selected_channel: str,
    user_id: str,
    error: Exception | None = None,
    failure_class: str | None = None,
    status: str = "failed",
) -> dict[str, object]:
    classified = failure_class if failure_class is not None else _failure_class(error or RuntimeError())
    if classified not in _SAFE_RUN_FAILURE_CLASSES or status not in {"failed", "not_run"}:
        raise ValueError("paid acceptance failure record is invalid")
    return {
        "phase": call.phase,
        "logical_model": call.model_id,
        "selected_channel": selected_channel,
        "status": status,
        "failure_class": classified,
        "user_id": user_id,
    }


def _summary_record(
    outcomes: tuple[dict[str, object], ...],
    *,
    user_id: str,
    attempted_calls: int,
) -> dict[str, object]:
    if (
        not 1 <= len(outcomes) <= 20
        or type(attempted_calls) is not int
        or not 0 <= attempted_calls <= len(outcomes)
    ):
        raise ValueError("paid acceptance summary count is invalid")
    distribution: dict[str, int] = {}
    durations: list[float] = []
    total_bytes = 0
    failure_classes: dict[str, int] = {}
    not_run_classes: dict[str, int] = {}
    projection: list[dict[str, object]] = []
    succeeded = 0
    failed = 0
    not_run = 0
    for record in outcomes:
        status = record.get("status")
        if status not in {"succeeded", "failed", "not_run"}:
            raise ValueError("paid acceptance summary outcome is invalid")
        channel = str(record["selected_channel"])
        item = {
            "phase": str(record["phase"]),
            "logical_model": str(record["logical_model"]),
            "selected_channel": channel,
            "status": status,
        }
        if status != "not_run":
            distribution[channel] = distribution.get(channel, 0) + 1
        if status == "succeeded":
            succeeded += 1
            durations.append(float(record["duration_seconds"]))
            total_bytes += int(record["bytes"])
        else:
            failure_class = str(record.get("failure_class", ""))
            if failure_class not in _SAFE_RUN_FAILURE_CLASSES:
                raise ValueError("paid acceptance summary failure class is invalid")
            item["failure_class"] = failure_class
            failure_classes[failure_class] = failure_classes.get(failure_class, 0) + 1
            if status == "failed":
                failed += 1
            else:
                not_run += 1
                not_run_classes[failure_class] = not_run_classes.get(failure_class, 0) + 1
        projection.append(item)
    return {
        "phase": "summary",
        "status": "succeeded" if succeeded == len(outcomes) else "failed",
        "planned_calls": len(outcomes),
        "attempted_calls": attempted_calls,
        "succeeded": succeeded,
        "failed": failed,
        "not_run": not_run,
        "channel_distribution": distribution,
        "failure_classes": failure_classes,
        "not_run_classes": not_run_classes,
        "latency_seconds": {
            "minimum": round(min(durations), 2) if durations else 0.0,
            "maximum": round(max(durations), 2) if durations else 0.0,
            "total": round(sum(durations), 2),
        },
        "bytes": total_bytes,
        "outcomes": projection,
        "user_id": user_id,
    }


class PaidRunRecorder:
    """Account for every planned call and emit exactly one redacted summary."""

    def __init__(
        self,
        plan: tuple[PaidCall, ...],
        *,
        user_id: str,
        emit: Callable[[str], None],
    ) -> None:
        if not plan or len(plan) > 20 or not callable(emit):
            raise ValueError("paid acceptance recorder is invalid")
        self._plan = plan
        self._user_id = user_id
        self._emit = emit
        self._outcomes: list[dict[str, object] | None] = [None] * len(plan)
        self._current_index: int | None = None
        self._next_index = 0
        self._attempted_calls = 0
        self._preflight_failure_class = "preflight_failed"
        self._stage = "preflight"
        self._finalized = False

    def __enter__(self) -> "PaidRunRecorder":
        return self

    def preflight(self, failure_class: str, action: Callable[[], object]) -> object:
        if failure_class not in {"upload_failed", "preflight_failed"} or self._stage != "preflight":
            raise ValueError("paid acceptance preflight is invalid")
        try:
            return action()
        except Exception:
            self._preflight_failure_class = failure_class
            raise

    def begin_call(self, call: PaidCall) -> None:
        if self._current_index is not None or self._next_index >= len(self._plan) or self._plan[self._next_index] != call:
            raise RuntimeError("paid acceptance call order is invalid")
        self._current_index = self._next_index
        self._stage = "activation"

    def mark_attempted(self) -> None:
        if self._current_index is None or self._stage != "activation":
            raise RuntimeError("paid acceptance attempt is invalid")
        self._attempted_calls += 1
        self._stage = "execution"

    def record_current(self, record: dict[str, object]) -> None:
        index = self._current_index
        if index is None or self._stage != "execution" or record.get("status") not in {"succeeded", "failed"}:
            raise RuntimeError("paid acceptance result record is invalid")
        self._outcomes[index] = dict(record)
        self._emit(render_record(record))
        self._advance()

    def fail_current(self, error: Exception, *, failure_class: str | None = None) -> None:
        index = self._current_index
        if index is None or self._stage not in {"activation", "execution"}:
            raise RuntimeError("paid acceptance failure stage is invalid")
        call = self._plan[index]
        record = _failure_record(
            call,
            selected_channel=call.channel_id or "unresolved",
            user_id=self._user_id,
            error=error,
            failure_class=failure_class,
        )
        self._outcomes[index] = record
        self._emit(render_record(record))
        self._advance()

    def _advance(self) -> None:
        self._next_index += 1
        self._current_index = None
        self._stage = "preflight" if self._next_index == 0 else "between_calls"

    def _fill_not_run(self, failure_class: str) -> None:
        for index, outcome in enumerate(self._outcomes):
            if outcome is not None:
                continue
            call = self._plan[index]
            self._outcomes[index] = _failure_record(
                call,
                selected_channel=call.channel_id or "unresolved",
                user_id=self._user_id,
                failure_class=failure_class,
                status="not_run",
            )

    def __exit__(self, error_type: object, error: BaseException | None, _traceback: object) -> bool:
        if self._finalized:
            return False
        if error is not None:
            if self._current_index is not None and self._outcomes[self._current_index] is None:
                safe_error = error if isinstance(error, Exception) else RuntimeError()
                failure_class = "activation_failed" if self._stage == "activation" else _failure_class(safe_error)
                self.fail_current(safe_error, failure_class=failure_class)
            if all(outcome is None for outcome in self._outcomes):
                self._fill_not_run(self._preflight_failure_class)
            else:
                self._fill_not_run("blocked_after_failure")
        else:
            self._fill_not_run("blocked_after_failure")
        outcomes = tuple(item for item in self._outcomes if item is not None)
        self._emit(render_record(_summary_record(outcomes, user_id=self._user_id, attempted_calls=self._attempted_calls)))
        self._finalized = True
        return False


def run_guarded_paid_acceptance(
    *,
    admin: ApiSession,
    user: ApiSession,
    data_dir: Path,
    channel_ids: tuple[str, ...],
    model_ids: tuple[str, ...],
    plan: tuple[PaidCall, ...],
    user_id: str,
    emit: Callable[[str], None],
) -> tuple[dict[str, object], ...]:
    from ai_creation_canvas.storage.sqlite import CanvasStore

    recorder = PaidRunRecorder(plan, user_id=user_id, emit=emit)
    with recorder:
        owned_asset_id = recorder.preflight("upload_failed", user.upload_reference_png)
        if not isinstance(owned_asset_id, str):
            raise RuntimeError("asset upload contract missing")
        if admin.request("GET", f"/api/v1/assets/{owned_asset_id}")[0] not in {403, 404}:
            raise RuntimeError("asset owner isolation failed")
        assigned = admin.json("PUT", f"/api/v1/admin/users/{user_id}/models", {"model_ids": list(model_ids)})
        if set(assigned.get("model_ids", [])) != set(model_ids):
            raise RuntimeError("model assignment failed")
        visible = user.json("GET", "/api/v1/models").get("models")
        if not isinstance(visible, list) or {item.get("model_id") for item in visible if isinstance(item, dict)} != set(model_ids):
            raise RuntimeError("model assignment isolation failed")
        store = CanvasStore(data_dir)
        banana_routes = {channel for channel in channel_ids if _CHANNEL_MODELS[channel] == "banana"}

        def activate_channel(channel_id: str) -> None:
            _set_model_routes(admin, _CHANNEL_MODELS[channel_id], {channel_id})

        def activate_banana() -> None:
            if not banana_routes:
                raise RuntimeError("Banana batch route is unavailable")
            _set_model_routes(admin, "banana", banana_routes)

        def execute(call: PaidCall) -> dict[str, object]:
            payload = request_for_paid_call(call, owned_asset_id)
            kind = "video" if payload["operation"] == "video.generate" else "image"
            result = _poll_and_download(user, admin, payload, kind)
            item, forbidden = store.job_for_owner(result.job_id, user_id)
            if forbidden or item is None or item.get("user_id") != user_id or item.get("logical_model_id") != call.model_id:
                raise RuntimeError("stored job owner contract failed")
            selected_channel = str(item.get("route_id", ""))
            if selected_channel not in channel_ids or call.channel_id is not None and selected_channel != call.channel_id:
                raise RuntimeError("stored job route contract failed")
            if item.get("idempotency_key") != payload["idempotency_key"]:
                raise RuntimeError("stored job idempotency contract failed")
            record = sanitized_result_record(
                phase=call.phase,
                logical_model=call.model_id,
                selected_channel=selected_channel,
                status="succeeded",
                mime=result.mime,
                byte_count=result.byte_count,
                duration_seconds=result.duration_seconds,
                user_id=user_id,
            )
            return record

        return execute_paid_plan(
            plan,
            activate_channel=activate_channel,
            activate_banana=activate_banana,
            execute=execute,
            recorder=recorder,
        )


def _serve_paid() -> None:
    import uvicorn

    root = Path(__file__).resolve().parents[1]
    data_dir = Path(os.environ["AICC_ACCEPTANCE_DATA"])
    pool_path = Path(os.environ["AICC_ACCEPTANCE_POOL_FILE"])
    port = int(os.environ["AICC_ACCEPTANCE_PORT"])
    channels = tuple(os.environ["AICC_ACCEPTANCE_CHANNEL_IDS"].split(","))
    app, accounts = create_acceptance_app(
        data_dir=data_dir,
        static_dir=root / "web/dist",
        port=port,
        credential_pool_path=pool_path,
        channel_ids=channels,
        chiyun_origin=os.environ.get("AICC_CHIYUN_BASE_URL", ""),
        t8star_origin=os.environ.get("AICC_T8STAR_BASE_URL", ""),
        maximum_provider_submissions=int(os.environ["AICC_MAX_PAID_CALLS"]),
    )
    if not accounts.created:
        raise RuntimeError("isolated acceptance accounts were not created")
    print(f"{accounts.admin_username}: {accounts.admin_password}", flush=True)
    print(f"{accounts.user_username}: {accounts.user_password}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _probe_file(value: str) -> None:
    path = Path(os.environ["AICC_ACCEPTANCE_SIGNAL_PROBE_FILE"])
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="ascii") as handle:
            os.chmod(temporary, 0o600)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _probe_signal_before_key() -> None:
    _probe_file("ready")
    while True:
        signal.pause()


def _probe_signal_server() -> None:
    key = consume_server_key()
    environment = server_environment(key)
    process = _spawn_child([sys.executable, "-c", "import time; time.sleep(300)"], cwd=Path.cwd(), environment=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    del key, environment
    _probe_file(str(process.pid))
    while True:
        signal.pause()


def main() -> int:
    global _owned_pool_file
    keys = consume_server_keys()
    root = Path(__file__).resolve().parents[1]
    data_dir = Path(os.environ["AICC_ACCEPTANCE_DATA"])
    port = int(os.environ["AICC_ACCEPTANCE_PORT"])
    origin = f"http://127.0.0.1:{port}"
    server_log = data_dir / ".server-bootstrap.log"
    server_log.touch(mode=0o600)
    channels = tuple(os.environ["AICC_ACCEPTANCE_CHANNEL_IDS"].split(","))
    model_ids = tuple(os.environ["AICC_ACCEPTANCE_MODEL_IDS"].split(","))
    plan = paid_call_plan(
        channels,
        banana_sample_count=int(os.environ["AICC_ACCEPTANCE_BANANA_SAMPLE_COUNT"]),
        maximum_paid_calls=int(os.environ["AICC_MAX_PAID_CALLS"]),
    )
    pool_path = data_dir / ".credential-pools.json"
    owned_pool = write_credential_pool_config(pool_path, channels, keys)
    _owned_pool_file = owned_pool
    environment = dict(os.environ)
    environment.pop("AICC_ACCEPTANCE_KEY_FILE", None)
    environment.pop("AICC_CHIYUN_BASE_URL", None)
    environment.pop("AICC_T8STAR_BASE_URL", None)
    environment["AICC_ACCEPTANCE_POOL_FILE"] = str(pool_path)
    environment["PYTHONUNBUFFERED"] = "1"
    with server_log.open("ab", buffering=0) as output:
        process = _spawn_child(
            [sys.executable, str(Path(__file__).resolve()), "--serve-paid"],
            cwd=root, environment=environment, stdout=output, stderr=output,
        )
        del keys, environment
        try:
            admin_password, user_password = _credentials(server_log, process)
            admin, user = ApiSession(origin), ApiSession(origin)
            admin.authenticate("canvas-admin", admin_password)
            user_info = user.authenticate("canvas-user", user_password)
            server_log.unlink(missing_ok=True)
            users = admin.json("GET", "/api/v1/admin/users").get("users", [])
            user_id = next(str(item["user_id"]) for item in users if isinstance(item, dict) and item.get("username") == "canvas-user")
            run_guarded_paid_acceptance(
                admin=admin,
                user=user,
                data_dir=data_dir,
                channel_ids=channels,
                model_ids=model_ids,
                plan=plan,
                user_id=user_id,
                emit=lambda line: print(line, flush=True),
            )
            del user_info
            return 0
        except (KeyError, StopIteration, ValueError, RuntimeError, URLError, json.JSONDecodeError):
            print('{"status":"failed","stage":"paid_acceptance"}', file=sys.stderr, flush=True)
            return 1
        finally:
            server_log.unlink(missing_ok=True)
            _stop_child()
            _remove_owned_file(owned_pool)
            if _owned_pool_file == owned_pool:
                _owned_pool_file = None


if __name__ == "__main__":
    _install_signal_cleanup()
    try:
        if sys.argv[1:] == ["--probe-key-boundary"]:
            probe_values = consume_server_keys()
            if not probe_values or any(
                name in os.environ
                for name in (
                    "ARK_API_KEY", "CHIYUN_API_KEY", "T8STAR_API_KEY",
                    "AICC_ACCEPTANCE_KEY_FILE", "AICC_ACCEPTANCE_POOL_FILE",
                    "AICC_CHIYUN_BASE_URL", "AICC_T8STAR_BASE_URL",
                )
            ):
                raise RuntimeError("paid credential boundary failed")
            del probe_values
            print("Paid acceptance key boundary ready. No provider request was made.")
            result = 0
        elif sys.argv[1:] == ["--probe-signal-before-key"]:
            _probe_signal_before_key()
            result = 1
        elif sys.argv[1:] == ["--probe-signal-server"]:
            _probe_signal_server()
            result = 1
        elif sys.argv[1:] == ["--serve-paid"]:
            _serve_paid()
            result = 0
        elif sys.argv[1:]:
            raise RuntimeError("unsupported acceptance argument")
        else:
            result = main()
    except Exception:
        print('{"status":"failed","stage":"paid_acceptance"}', file=sys.stderr, flush=True)
        result = 1
    finally:
        _stop_child()
        _remove_credential_file()
    raise SystemExit(result)

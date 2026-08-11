"""One-shot, API-level paid acceptance runner. Never import this from the server."""

from __future__ import annotations

from http.cookiejar import CookieJar
import binascii
import json
import os
from pathlib import Path
import re
import secrets
import struct
import subprocess
import sys
import time
import zlib
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener


def paid_image_request(image_model: str, owned_asset_id: str) -> dict[str, object]:
    return {"operation": "image.edit", "model_id": image_model, "prompt": "Keep the green circle and place it on a clean black background.", "params": {"size": "1024x1024"}, "asset_ids": [], "inputs": {"reference_images": [owned_asset_id]}, "idempotency_key": secrets.token_urlsafe(24)}


def paid_video_request(video_model: str, image_result_asset_id: str) -> dict[str, object]:
    return {"operation": "video.generate", "model_id": video_model, "prompt": "The green circle moves slowly across the black background.", "params": {"ratio": "16:9", "resolution": "480p", "duration": 5, "generate_audio": False, "watermark": False, "return_last_frame": False}, "asset_ids": [], "inputs": {"reference_images": [image_result_asset_id]}, "idempotency_key": secrets.token_urlsafe(24)}


def paid_job_requests(image_model: str, video_model: str, owned_asset_id: str, image_result_asset_id: str) -> tuple[dict[str, object], dict[str, object]]:
    return paid_image_request(image_model, owned_asset_id), paid_video_request(video_model, image_result_asset_id)


def sanitized_result_record(*, kind: str, job_id: str, model_id: str, status: str, mime: str, byte_count: int, duration_seconds: float) -> dict[str, object]:
    return {"kind": kind, "job_id": job_id, "model_id": model_id, "status": status, "mime": mime, "bytes": byte_count, "duration_seconds": round(duration_seconds, 2)}


def render_record(record: dict[str, object]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def reference_png() -> bytes:
    """Return a tiny valid 64x64 RGB PNG above the provider's 14px floor."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\x00\xff\x55" * 64
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(row * 64, 9)) + chunk(b"IEND", b"")


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
            raise RuntimeError(f"api status {status}")
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
        with self.opener.open(request, timeout=180) as response:
            mime = response.headers.get_content_type()
            total = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
            return mime, total


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


def _poll_and_download(user: ApiSession, other: ApiSession, payload: dict[str, object], kind: str) -> tuple[dict[str, object], str, str]:
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
        raise RuntimeError("generation did not succeed")
    results = state.get("results")
    if not isinstance(results, list) or len(results) < 1 or not isinstance(results[0], dict):
        raise RuntimeError("result contract missing")
    result_url, result_asset_id = str(results[0].get("url", "")), str(results[0].get("asset_id", ""))
    if other.request("GET", result_url)[0] != 404:
        raise RuntimeError("result owner isolation failed")
    head_status, head_headers, _ = user.request("HEAD", result_url)
    range_status, range_headers, range_body = user.request("GET", result_url, extra_headers={"Range": "bytes=0-1023"})
    if head_status != 200 or range_status != 206 or not range_body or "content-range" not in range_headers:
        raise RuntimeError("result streaming contract failed")
    mime, byte_count = user.download(result_url)
    if head_headers.get("content-type", "").split(";", 1)[0] != mime:
        raise RuntimeError("result MIME contract failed")
    record = sanitized_result_record(kind=kind, job_id=job_id, model_id=str(payload["model_id"]), status="succeeded", mime=mime, byte_count=byte_count, duration_seconds=time.monotonic() - started)
    return record, result_asset_id, result_url


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


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    data_dir = Path(os.environ["AICC_ACCEPTANCE_DATA"])
    port = int(os.environ["AICC_ACCEPTANCE_PORT"])
    origin = f"http://127.0.0.1:{port}"
    server_log = data_dir / ".server-bootstrap.log"
    server_log.touch(mode=0o600)
    environment = dict(os.environ)
    environment["PYTHONUNBUFFERED"] = "1"
    with server_log.open("ab", buffering=0) as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "ai_creation_canvas", "serve-local", "--port", str(port), "--data-dir", str(data_dir), "--static-dir", str(root / "web/dist"), "--ark-models", os.environ["AICC_ACCEPTANCE_MODELS_CONFIG"], "--bootstrap-if-empty"],
            cwd=root, env=environment, stdout=output, stderr=output,
        )
        try:
            admin_password, user_password = _credentials(server_log, process)
            admin, user = ApiSession(origin), ApiSession(origin)
            admin.authenticate("canvas-admin", admin_password)
            user_info = user.authenticate("canvas-user", user_password)
            server_log.unlink(missing_ok=True)
            users = admin.json("GET", "/api/v1/admin/users").get("users", [])
            user_id = next(str(item["user_id"]) for item in users if isinstance(item, dict) and item.get("username") == "canvas-user")
            model_ids = [os.environ["AICC_ACCEPTANCE_IMAGE_MODEL_ID"], os.environ["AICC_ACCEPTANCE_VIDEO_MODEL_ID"]]
            admin.json("PUT", f"/api/v1/admin/users/{user_id}/models", {"model_ids": model_ids})
            visible = user.json("GET", "/api/v1/models").get("models", [])
            if {item.get("model_id") for item in visible if isinstance(item, dict)} != set(model_ids):
                raise RuntimeError("model assignment isolation failed")
            owned_asset_id = user.upload_reference_png()
            if admin.request("GET", f"/api/v1/assets/{owned_asset_id}")[0] not in {403, 404}:
                raise RuntimeError("asset owner isolation failed")
            project = _project_document(owned_asset_id, *model_ids, len(reference_png()))
            persisted = user.json("POST", "/api/v1/projects", project, expected=(201,))
            saved = persisted.get("project")
            if not isinstance(saved, dict) or saved.get("nodes") != project["nodes"] or saved.get("connections") != project["connections"]:
                raise RuntimeError("canvas graph persistence failed")
            image_request = paid_image_request(model_ids[0], owned_asset_id)
            image_record, image_result_asset_id, _image_result_url = _poll_and_download(user, admin, image_request, "image")
            print(render_record(image_record), flush=True)
            video_request = paid_video_request(model_ids[1], image_result_asset_id)
            video_record, _video_result_asset_id, _video_result_url = _poll_and_download(user, admin, video_request, "video")
            print(render_record(video_record), flush=True)
            del user_info
            return 0
        except (KeyError, StopIteration, ValueError, RuntimeError, URLError, json.JSONDecodeError):
            print('{"status":"failed","stage":"paid_acceptance"}', file=sys.stderr, flush=True)
            return 1
        finally:
            server_log.unlink(missing_ok=True)
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)


if __name__ == "__main__":
    try:
        result = main()
    except Exception:
        print('{"status":"failed","stage":"paid_acceptance"}', file=sys.stderr, flush=True)
        result = 1
    raise SystemExit(result)

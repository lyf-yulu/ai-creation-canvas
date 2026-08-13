from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time

import pytest

from tests.server.test_model_assignments import local_clients


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("acceptance_real_media", ROOT / "scripts/acceptance_real_media.py")
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_paid_job_payloads_are_exactly_one_single_image_and_one_5s_480p_video() -> None:
    requests = module.paid_job_requests("doubao-seedream-4-0-250828", "doubao-seedance-2-0-260128", "owned-asset", "job-result.image.0")
    assert len(requests) == 2
    assert requests[0]["operation"] == "image.edit"
    assert requests[0]["params"] == {"size": "1024x1024"}
    assert requests[0]["inputs"] == {"reference_images": ["owned-asset"]}
    assert "n" not in requests[0]["params"]
    assert requests[1]["operation"] == "video.generate"
    assert requests[1]["params"] == {"ratio": "16:9", "resolution": "480p", "duration": 5, "generate_audio": False, "watermark": False, "return_last_frame": False}
    assert requests[1]["inputs"] == {"reference_images": ["job-result.image.0"]}


def test_reviewed_channel_definitions_use_only_code_owned_models_and_origins() -> None:
    definitions = module.acceptance_definitions(
        ("banana-chiyun", "banana-t8star", "seedance-ark"),
        chiyun_origin="https://chiyun.example",
        t8star_origin="https://t8star.example",
    )

    assert {item["provider_id"] for item in definitions["providers"]} == {"chiyun", "t8star", "ark"}
    assert {item["model_id"] for item in definitions["models"]} == {"banana", "seedance"}
    routes = {item["route_id"]: item for item in definitions["routes"]}
    assert set(routes) == {"banana-chiyun", "banana-t8star", "seedance-ark"}
    assert routes["banana-chiyun"]["provider_model_name"] == "gemini-2.5-flash-image"
    assert routes["banana-t8star"]["provider_model_name"] == "gemini-2.5-flash-image"
    assert routes["seedance-ark"]["provider_model_name"] == "doubao-seedance-2-5-260628"
    assert routes["seedance-ark"]["operation_contracts"][0]["operation"] == "video.generate"
    encoded = json.dumps(definitions, sort_keys=True)
    for forbidden in ("api_key", "authorization", "secret"):
        assert forbidden not in encoded.lower()


def test_each_paid_call_uses_the_minimum_reviewed_request_shape() -> None:
    owned_asset = "owned-asset"
    banana = module.request_for_paid_call(module.PaidCall("smoke", "banana-chiyun", "banana"), owned_asset)
    seedream = module.request_for_paid_call(module.PaidCall("smoke", "seedream-ark", "seedream"), owned_asset)
    seedance = module.request_for_paid_call(module.PaidCall("smoke", "seedance-ark", "seedance"), owned_asset)

    assert banana["operation"] == "image.edit"
    assert banana["params"] == {"size": "1024x1024", "output_count": 1}
    assert banana["inputs"] == {"reference_images": [owned_asset]}
    assert seedream["operation"] == "image.edit"
    assert seedream["params"] == {"size": "1K", "watermark": False, "output_format": "png", "prompt_optimization": "fast"}
    assert seedream["inputs"] == {"reference_images": [owned_asset]}
    assert seedance["operation"] == "video.generate"
    assert seedance["params"] == {"ratio": "16:9", "resolution": "480p", "duration": 5, "generate_audio": False, "watermark": False, "return_last_frame": False}
    assert seedance["inputs"] == {}
    assert len({banana["idempotency_key"], seedream["idempotency_key"], seedance["idempotency_key"]}) == 3


def test_acceptance_log_projection_cannot_include_sensitive_request_fields() -> None:
    record = module.sanitized_result_record(
        phase="smoke", logical_model="banana", selected_channel="banana-chiyun",
        status="succeeded", mime="image/png", byte_count=123,
        duration_seconds=1.25, user_id="user-safe",
    )
    assert record == {
        "phase": "smoke",
        "logical_model": "banana",
        "selected_channel": "banana-chiyun",
        "status": "succeeded",
        "mime": "image/png",
        "bytes": 123,
        "duration_seconds": 1.25,
        "user_id": "user-safe",
    }
    serialized = module.render_record(record)
    for forbidden in ("key", "prompt", "cookie", "url", "authorization", "secret"):
        assert forbidden not in serialized.lower()


def test_paid_call_plan_runs_every_channel_smoke_before_the_banana_sample() -> None:
    plan = module.paid_call_plan(
        ("banana-chiyun", "banana-t8star", "seedance-ark"),
        banana_sample_count=2,
        maximum_paid_calls=5,
    )

    assert [(item.phase, item.channel_id, item.model_id) for item in plan] == [
        ("smoke", "banana-chiyun", "banana"),
        ("smoke", "banana-t8star", "banana"),
        ("smoke", "seedance-ark", "seedance"),
        ("banana_sample", None, "banana"),
        ("banana_sample", None, "banana"),
    ]


def test_paid_call_plan_rejects_unknown_channels_or_a_plan_over_twenty() -> None:
    with pytest.raises(ValueError, match="channel"):
        module.paid_call_plan(("banana-unknown",), banana_sample_count=0, maximum_paid_calls=1)
    with pytest.raises(ValueError, match="budget"):
        module.paid_call_plan(("banana-chiyun",), banana_sample_count=20, maximum_paid_calls=20)


def test_banana_batch_is_never_enabled_after_a_failed_channel_smoke() -> None:
    plan = module.paid_call_plan(
        ("banana-chiyun", "seedance-ark"),
        banana_sample_count=2,
        maximum_paid_calls=4,
    )
    events: list[str] = []

    def activate(channel_id: str) -> None:
        events.append(f"activate:{channel_id}")

    def activate_banana() -> None:
        events.append("activate:banana-batch")

    def execute(call) -> dict[str, object]:
        events.append(f"execute:{call.phase}:{call.channel_id or 'auto'}")
        if call.channel_id == "seedance-ark":
            raise RuntimeError("smoke failed")
        return {"status": "succeeded"}

    with pytest.raises(RuntimeError, match="smoke failed"):
        module.execute_paid_plan(plan, activate_channel=activate, activate_banana=activate_banana, execute=execute)

    assert events == [
        "activate:banana-chiyun",
        "execute:smoke:banana-chiyun",
        "activate:seedance-ark",
        "execute:smoke:seedance-ark",
    ]


def test_paid_reference_png_meets_provider_minimum_dimensions(tmp_path: Path) -> None:
    image = module.reference_png()
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", image[16:24])
    assert width >= 16 and height >= 16
    path = tmp_path / "reference.png"
    path.write_bytes(image)
    assert module.verify_media_file(path, "image/png", "image") == {"codec_type": "video", "width": 64, "height": 64}


def test_acceptance_project_persists_a_canonical_edit_and_video_graph() -> None:
    project = module._project_document("owned-asset", "image-model", "video-model", 123)
    assert all({"position", "width", "height", "metadata"}.issubset(node) for node in project["nodes"])
    graphs = {node["id"]: node["metadata"]["graph"] for node in project["nodes"]}
    assert graphs["prompt"] == {"schemaVersion": 1, "role": "prompt", "text": "Paid acceptance prompt", "outputPortId": "prompt"}
    assert graphs["reference"]["items"][0] == {"id": "owned-item", "assetId": "owned-asset", "displayName": "reference.png", "mimeType": "image/png", "bytes": 123, "width": 64, "height": 64}
    assert graphs["image-model"]["operation"] == "image.edit"
    assert graphs["video-model"]["parameters"]["resolution"] == "480p"
    assert project["connections"] == [
        {"id": "prompt-image", "fromNodeId": "prompt", "fromPortId": "prompt", "toNodeId": "image-model", "toPortId": "prompt"},
        {"id": "reference-image", "fromNodeId": "reference", "fromPortId": "media", "toNodeId": "image-model", "toPortId": "reference_images"},
        {"id": "prompt-video", "fromNodeId": "prompt", "fromPortId": "prompt", "toNodeId": "video-model", "toPortId": "prompt"},
    ]


def test_acceptance_project_round_trips_through_the_owned_api(tmp_path: Path) -> None:
    _app, _accounts, admin, user, _admin_headers, user_headers = local_clients(tmp_path)
    project = module._project_document("owned-asset", "image-model", "video-model", 123)
    response = user.post("/api/v1/projects", headers=user_headers, json=project)
    assert response.status_code == 201
    assert response.json()["project"]["nodes"] == project["nodes"]
    assert response.json()["project"]["connections"] == project["connections"]
    assert admin.get("/api/v1/projects/paid-acceptance-canvas").status_code == 404


def test_two_paid_poll_flows_keep_the_result_reference_chain_and_stream_checks(monkeypatch) -> None:
    requests = module.paid_job_requests("image-model", "video-model", "owned-asset", "job-result.image-job.0")

    class User:
        def __init__(self): self.posts = []
        def json(self, method, path, payload=None, expected=(200,)):
            if method == "POST":
                self.posts.append(payload)
                job = "image-job" if payload["operation"] == "image.edit" else "video-job"
                repeats = sum(1 for item in self.posts if item["operation"] == payload["operation"])
                return {"id": job, "status": "queued" if repeats == 1 else "succeeded"}
            job = path.rsplit("/", 1)[-1]
            kind = "image" if job == "image-job" else "video"
            return {"id": job, "status": "succeeded", "results": [{"url": f"/api/v1/results/{job}/0", "asset_id": f"job-result.{job}.0", "media_type": kind}]}
        def request(self, method, path, payload=None, extra_headers=None):
            mime = "image/png" if "image-job" in path else "video/mp4"
            if method == "HEAD": return 200, {"content-type": mime}, b""
            assert extra_headers == {"Range": "bytes=0-1023"}
            return 206, {"content-range": "bytes 0-2/3"}, b"abc"
        def download(self, path): return ("image/png" if "image-job" in path else "video/mp4"), 3

    class Other:
        def request(self, method, path): return 404, {}, b""

    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    user = User(); other = Other()
    image = module._poll_and_download(user, other, requests[0], "image")
    assert image.result_asset_id == "job-result.image-job.0"
    video = module._poll_and_download(user, other, requests[1], "video")
    assert video.result_asset_id == "job-result.video-job.0"
    assert len(user.posts) == 4
    assert user.posts[0] == user.posts[1]
    assert user.posts[2] == user.posts[3]
    assert user.posts[2]["inputs"]["reference_images"] == [image.result_asset_id]


def test_runner_consumes_a_mode_0600_multi_key_bundle_once(tmp_path: Path, monkeypatch) -> None:
    key_file = tmp_path / "paid-keys.json"
    key_file.write_text(
        json.dumps({"ARK_API_KEY": "sentinel-ark-key", "CHIYUN_API_KEY": "sentinel-chiyun-key"}),
        encoding="utf-8",
    )
    key_file.chmod(0o600)
    monkeypatch.setenv("AICC_ACCEPTANCE_KEY_FILE", str(key_file))
    for name in ("ARK_API_KEY", "CHIYUN_API_KEY", "T8STAR_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    values = module.consume_server_keys()

    assert set(values) == {"ARK_API_KEY", "CHIYUN_API_KEY"}
    assert not key_file.exists()
    assert "AICC_ACCEPTANCE_KEY_FILE" not in os.environ


def test_acceptance_server_bootstrap_validates_every_selected_route_without_provider_io(tmp_path: Path) -> None:
    channels = ("banana-chiyun", "banana-t8star", "seedream-ark", "seedance-ark")
    data_dir = tmp_path / "paid-data"
    data_dir.mkdir(mode=0o700)
    pool_file = data_dir / ".credential-pools.json"
    module.write_credential_pool_config(
        pool_file,
        channels,
        {
            "CHIYUN_API_KEY": "offline-chiyun-key",
            "T8STAR_API_KEY": "offline-t8star-key",
            "ARK_API_KEY": "offline-ark-key",
        },
    )

    app, accounts = module.create_acceptance_app(
        data_dir=data_dir,
        static_dir=tmp_path / "dist",
        port=8998,
        credential_pool_path=pool_file,
        channel_ids=channels,
        chiyun_origin="https://chiyun.example",
        t8star_origin="https://t8star.example",
    )

    assert accounts.created is True
    assert {item.model_id for item in app.state.canvas_store.list_logical_models()} == {"banana", "seedream", "seedance"}
    routes = app.state.canvas_store.list_model_routes(include_archived=False)
    assert {item.route_id for item in routes} == set(channels)
    assert sum(item.enabled for item in routes if item.model_id == "banana") == 1
    for route in routes:
        app.state.managed_routing_runtime.adapter_factory.validate_route(route)
    summaries = app.state.managed_routing_runtime.pools()
    assert set(summaries) == {f"paid-{channel}" for channel in channels}
    assert pool_file.stat().st_mode & 0o777 == 0o600


def test_real_runner_checks_project_owner_before_first_paid_post(monkeypatch) -> None:
    events: list[str] = []

    class User:
        def upload_reference_png(self): events.append("upload"); return "owned-asset"
        def json(self, method, path, payload=None, expected=(200,)):
            assert method == "POST" and path == "/api/v1/projects"
            events.append("project-post")
            return {"project": payload}

    class Admin:
        def request(self, method, path):
            events.append(f"admin-get:{path}")
            return (403 if path.startswith("/api/v1/assets/") else 404), {}, b""

    def paid(_user, _admin, payload, kind):
        events.append(f"paid:{kind}")
        token = "job-result.image-job.0" if kind == "image" else "job-result.video-job.0"
        return module.sanitized_result_record(
            phase="smoke", logical_model=str(payload["model_id"]), selected_channel=f"{kind}-channel",
            status="succeeded", mime=f"{kind}/fixture", byte_count=3, duration_seconds=1,
            user_id="user-safe",
        ), token, f"/api/v1/results/{kind}-job/0"

    monkeypatch.setattr(module, "_poll_and_download", paid)
    emitted: list[str] = []
    module.run_paid_graph(User(), Admin(), ["image-model", "video-model"], emitted.append)

    assert events == [
        "upload",
        "admin-get:/api/v1/assets/owned-asset",
        "project-post",
        "admin-get:/api/v1/projects/paid-acceptance-canvas",
        "paid:image",
        "paid:video",
    ]


def test_runner_signal_before_key_read_removes_credential_file(tmp_path: Path) -> None:
    key_file = tmp_path / "paid-key"
    key_file.write_text("sentinel-paid-key", encoding="utf-8"); key_file.chmod(0o600)
    ready = tmp_path / "ready"
    environment = {**os.environ, "AICC_ACCEPTANCE_KEY_FILE": str(key_file), "AICC_ACCEPTANCE_SIGNAL_PROBE_FILE": str(ready)}
    environment.pop("ARK_API_KEY", None)
    process = subprocess.Popen([sys.executable, str(ROOT / "scripts/acceptance_real_media.py"), "--probe-signal-before-key"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for _ in range(100):
        if ready.exists(): break
        time.sleep(0.01)
    assert ready.exists()
    process.send_signal(signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode != 0
    assert not key_file.exists()
    assert "sentinel-paid-key" not in stdout + stderr


def test_runner_signal_with_server_child_removes_key_and_child(tmp_path: Path) -> None:
    key_file = tmp_path / "paid-key"
    key_file.write_text(json.dumps({"ARK_API_KEY": "sentinel-paid-key"}), encoding="utf-8"); key_file.chmod(0o600)
    child_pid_file = tmp_path / "child-pid"
    environment = {**os.environ, "AICC_ACCEPTANCE_KEY_FILE": str(key_file), "AICC_ACCEPTANCE_SIGNAL_PROBE_FILE": str(child_pid_file)}
    for name in ("ARK_API_KEY", "CHIYUN_API_KEY", "T8STAR_API_KEY"):
        environment.pop(name, None)
    process = subprocess.Popen([sys.executable, str(ROOT / "scripts/acceptance_real_media.py"), "--probe-signal-server"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for _ in range(100):
        if child_pid_file.exists(): break
        time.sleep(0.01)
    assert child_pid_file.exists()
    child_pid = int(child_pid_file.read_text(encoding="ascii"))
    process.send_signal(signal.SIGHUP)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode != 0
    assert not key_file.exists()
    for _ in range(100):
        try: os.kill(child_pid, 0)
        except ProcessLookupError: break
        time.sleep(0.01)
    else: raise AssertionError("server child survived acceptance runner signal")
    assert "sentinel-paid-key" not in stdout + stderr

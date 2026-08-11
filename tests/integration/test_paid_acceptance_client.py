from __future__ import annotations

import importlib.util
from pathlib import Path
import struct

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


def test_acceptance_log_projection_cannot_include_sensitive_request_fields() -> None:
    record = module.sanitized_result_record(
        kind="image", job_id="job-safe", model_id="model-safe", status="succeeded",
        mime="image/png", byte_count=123, duration_seconds=1.25,
    )
    assert record == {"kind": "image", "job_id": "job-safe", "model_id": "model-safe", "status": "succeeded", "mime": "image/png", "bytes": 123, "duration_seconds": 1.25}
    serialized = module.render_record(record)
    for forbidden in ("key", "prompt", "cookie", "url", "authorization", "secret"):
        assert forbidden not in serialized.lower()


def test_paid_reference_png_meets_provider_minimum_dimensions() -> None:
    image = module.reference_png()
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", image[16:24])
    assert width >= 16 and height >= 16


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
                return {"id": job, "status": "queued"}
            job = path.rsplit("/", 1)[-1]
            kind = "image" if job == "image-job" else "video"
            return {"id": job, "status": "succeeded", "results": [{"url": f"/api/v1/results/{job}/0", "asset_id": f"job-result.{job}.0", "media_type": kind}]}
        def request(self, method, path, payload=None, extra_headers=None):
            if method == "HEAD": return 200, {"content-type": "image/png"}, b""
            assert extra_headers == {"Range": "bytes=0-1023"}
            return 206, {"content-range": "bytes 0-2/3"}, b"abc"
        def download(self, path): return "image/png", 3

    class Other:
        def request(self, method, path): return 404, {}, b""

    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    user = User(); other = Other()
    image = module._poll_and_download(user, other, requests[0], "image")
    assert image[1] == "job-result.image-job.0"
    video = module._poll_and_download(user, other, requests[1], "video")
    assert video[1] == "job-result.video-job.0"
    assert len(user.posts) == 2
    assert user.posts[1]["inputs"]["reference_images"] == [image[1]]

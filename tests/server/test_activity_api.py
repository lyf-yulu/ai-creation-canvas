from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

from fastapi.testclient import TestClient

from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.storage.sqlite import CanvasStore


def identity(user_id: str, username: str = "Test User") -> dict[str, str]:
    timestamp = str(int(time.time()))
    payload = f"v2\n{timestamp}\n{user_id}\nuser\n{quote(username, safe='')}"
    signature = hmac.new(b"test-secret", payload.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Portal-Sig-Version": "2",
        "X-Portal-Timestamp": timestamp,
        "X-Portal-User-Id": user_id,
        "X-Portal-Username": username,
        "X-Portal-Role": "user",
        "X-Portal-Signature": signature,
    }


def test_activity_lists_only_current_owner_metadata(tmp_path) -> None:
    store = CanvasStore(tmp_path / "data")
    app = create_app(
        Settings("test", 8992, tmp_path / "data", "test-secret"),
        static_dir=tmp_path / "missing-static",
        canvas_store=store,
    )
    client = TestClient(app)
    store.create_asset(asset_id="asset-a", user_id="user-a", kind="reference", mime_type="image/png", relative_path="assets/a.png", size_bytes=12)
    store.create_asset(asset_id="asset-b", user_id="user-b", kind="reference", mime_type="image/png", relative_path="assets/b.png", size_bytes=24)
    store.reserve_job(user_id="user-a", job_id="job-a", service_id="demo", operation="image.generate", idempotency_key="key-a", request_hash="hash-a")
    store.reserve_job(user_id="user-b", job_id="job-b", service_id="demo", operation="image.generate", idempotency_key="key-b", request_hash="hash-b")

    assets = client.get("/api/v1/activity/assets?user_id=user-b", headers=identity("user-a"))
    jobs = client.get("/api/v1/activity/jobs?user_id=user-b", headers=identity("user-a"))

    assert assets.status_code == jobs.status_code == 200
    assert [item["asset_id"] for item in assets.json()["assets"]] == ["asset-a"]
    assert [item["id"] for item in jobs.json()["jobs"]] == ["job-a"]
    assert "relative_path" not in assets.text
    assert "request_hash" not in jobs.text
    assert "user-b" not in assets.text + jobs.text


def test_activity_list_is_capped_at_one_hundred(tmp_path) -> None:
    store = CanvasStore(tmp_path / "data")
    app = create_app(
        Settings("test", 8992, tmp_path / "data", "test-secret"),
        static_dir=tmp_path / "missing-static",
        canvas_store=store,
    )
    client = TestClient(app)
    for index in range(105):
        store.create_asset(asset_id=f"asset-{index:03d}", user_id="user-a", kind="reference", mime_type="image/png", relative_path=f"assets/{index:03d}.png", size_bytes=index + 1)

    response = client.get("/api/v1/activity/assets", headers=identity("user-a"))

    assert response.status_code == 200
    assert len(response.json()["assets"]) == 100


def test_usage_lists_only_current_owner_charged_jobs(tmp_path) -> None:
    store = CanvasStore(tmp_path / "data")
    app = create_app(
        Settings("test", 8992, tmp_path / "data", "test-secret"),
        static_dir=tmp_path / "missing-static",
        canvas_store=store,
    )
    client = TestClient(app)
    first = store.reserve_job(
        user_id="user-a",
        job_id="job-a",
        service_id="demo",
        operation="image.generate",
        idempotency_key="key-a",
        request_hash="hash-a",
        image_count=1,
    )
    second = store.reserve_job(
        user_id="user-b",
        job_id="job-b",
        service_id="demo",
        operation="image.generate",
        idempotency_key="key-b",
        request_hash="hash-b",
        image_count=1,
    )
    store.mark_submitted("job-a", "upstream-a", "succeeded", str(first.job["submission_token"]))
    store.mark_submitted("job-b", "upstream-b", "succeeded", str(second.job["submission_token"]))

    response = client.get("/api/v1/usage", headers=identity("user-a"))

    assert response.status_code == 200
    assert response.json()["summary"] == {
        "successful_jobs": 1,
        "image_count": 1,
        "video_seconds": 0,
        "total_cost_fen": 0,
    }
    assert len(response.json()["jobs"]) == 1
    assert "request_hash" not in response.text
    assert "user-b" not in response.text

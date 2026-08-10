from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import PortalRole
from tests.server.test_model_assignments import ORIGIN


def test_complete_offline_demo_flow_is_owned_and_idempotent(tmp_path) -> None:
    settings = Settings(
        "test", 8992, tmp_path / "data", "test-secret",
        identity_mode="local", allowed_origins=(ORIGIN,), enable_demo_adapter=True,
    )
    app = create_app(settings, static_dir=tmp_path / "dist")
    accounts = app.state.local_auth.bootstrap_accounts(("demo-image-v1",))
    assert accounts.user is not None
    app.state.local_auth.create_user("canvas-user-b", "普通用户 B", "correct-horse-battery", PortalRole.USER, must_change_password=False)

    user_a = TestClient(app, base_url=ORIGIN)
    user_b = TestClient(app, base_url=ORIGIN)
    login_a = user_a.post("/api/v1/auth/login", json={"username": accounts.user_username, "password": accounts.user_password}).json()
    login_b = user_b.post("/api/v1/auth/login", json={"username": "canvas-user-b", "password": "correct-horse-battery"}).json()
    headers_a = {"Origin": ORIGIN, "X-CSRF-Token": login_a["csrf_token"]}
    del login_b

    models = user_a.get("/api/v1/models").json()["models"]
    assert [model["model_id"] for model in models] == ["demo-image-v1"]
    payload = {
        "operation": "image.generate", "model_id": "demo-image-v1", "prompt": "黑绿科技产品海报",
        "params": {"aspect_ratio": "landscape"}, "asset_ids": [], "idempotency_key": "demo-once",
    }
    created = user_a.post("/api/v1/jobs", headers=headers_a, json=payload)
    repeated = user_a.post("/api/v1/jobs", headers=headers_a, json=payload)
    assert created.status_code == repeated.status_code == 201
    assert created.json()["id"] == repeated.json()["id"]
    job_id = created.json()["id"]

    finished = user_a.get(f"/api/v1/jobs/{job_id}")
    assert finished.status_code == 200
    assert finished.json()["status"] == "succeeded"
    assert finished.json()["result_url"] == f"/api/v1/results/{job_id}"
    full = user_a.get(f"/api/v1/results/{job_id}")
    head = user_a.head(f"/api/v1/results/{job_id}")
    ranged = user_a.get(f"/api/v1/results/{job_id}", headers={"Range": "bytes=0-9"})
    assert full.status_code == head.status_code == 200
    assert full.headers["content-type"] == "image/png"
    assert head.content == b""
    assert int(head.headers["content-length"]) == len(full.content)
    assert ranged.status_code == 206 and len(ranged.content) == 10

    assert user_b.get(f"/api/v1/jobs/{job_id}").status_code == 404
    assert user_b.get(f"/api/v1/results/{job_id}").status_code == 404
    with sqlite3.connect(app.state.canvas_store.database) as db:
        assert db.execute("SELECT COUNT(*) FROM canvas_jobs WHERE idempotency_key='demo-once'").fetchone()[0] == 1

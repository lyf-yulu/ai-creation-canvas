from __future__ import annotations

import sqlite3

from tests.server.test_model_assignments import local_clients


def test_admin_user_list_contains_only_safe_management_fields(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, user, admin_headers, user_headers

    response = admin.get("/api/v1/admin/users")

    assert response.status_code == 200
    assert len(response.json()["users"]) == 2
    assert set(response.json()["users"][0]) == {
        "user_id",
        "username",
        "display_name",
        "role",
        "enabled",
        "must_change_password",
        "model_ids",
        "created_at",
        "updated_at",
    }
    lowered = response.text.lower()
    assert "password_hash" not in lowered
    assert "csrf" not in lowered
    assert "session" not in lowered
    assert "secret" not in lowered


def test_admin_can_disable_user_and_revoke_their_session(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, user_headers
    assert accounts.user is not None

    response = admin.patch(
        f"/api/v1/admin/users/{accounts.user.user_id}",
        headers=admin_headers,
        json={"enabled": False},
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert user.get("/api/v1/session").status_code == 401


def test_admin_usage_aggregates_jobs_by_server_owned_user_id(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    assert accounts.admin is not None and accounts.user is not None
    database = app.state.canvas_store.database
    with sqlite3.connect(database) as db:
        rows = [
            ("job-user-image", accounts.user.user_id, "image", "image.edit", "succeeded", "key-1"),
            ("job-user-video", accounts.user.user_id, "video", "video.generate", "running", "key-2"),
            ("job-user-failed", accounts.user.user_id, "image", "image.generate", "failed", "key-3"),
            ("job-admin-video", accounts.admin.user_id, "video", "video.generate", "succeeded", "key-4"),
        ]
        db.executemany(
            "INSERT INTO canvas_jobs(id,user_id,service_id,operation,status,idempotency_key,request_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'2026-08-13T00:00:00Z','2026-08-13T00:00:00Z')",
            ((job_id, user_id, service, operation, status, key, f"hash-{key}") for job_id, user_id, service, operation, status, key in rows),
        )

    response = admin.get("/api/v1/admin/usage")

    assert response.status_code == 200
    body = response.json()
    assert body["totals"] == {"jobs": 4, "succeeded": 2, "failed": 1, "active": 1, "image": 2, "video": 2}
    by_id = {item["user_id"]: item for item in body["users"]}
    assert by_id[accounts.user.user_id]["username"] == "canvas-user"
    assert by_id[accounts.user.user_id]["display_name"]
    assert {key: by_id[accounts.user.user_id][key] for key in ("jobs", "succeeded", "failed", "active", "image", "video")} == {
        "jobs": 3, "succeeded": 1, "failed": 1, "active": 1, "image": 2, "video": 1,
    }
    assert by_id[accounts.admin.user_id]["jobs"] == 1
    assert user.get("/api/v1/admin/usage", headers=user_headers).status_code == 404
    assert "idempotency" not in response.text.lower()

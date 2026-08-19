from __future__ import annotations

import sqlite3

import pytest

from ai_creation_canvas.api.auth import _register_rate_limiter
from tests.server.test_model_assignments import ORIGIN, local_clients


@pytest.fixture(autouse=True)
def reset_register_rate_limiter():
    _register_rate_limiter.reset()
    yield
    _register_rate_limiter.reset()


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
        "approval_status",
        "model_ids",
        "comfy_workflow_ids",
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


def test_admin_can_set_a_regular_users_password_and_revokes_sessions(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del user_headers
    assert accounts.admin is not None and accounts.user is not None

    response = admin.post(
        f"/api/v1/admin/users/{accounts.user.user_id}/password",
        headers=admin_headers,
        json={"new_password": "admin-issued-correct-horse", "must_change_password": False},
    )

    assert response.status_code == 200
    assert set(response.json()) == {
        "user_id",
        "username",
        "display_name",
        "role",
        "enabled",
        "must_change_password",
        "approval_status",
        "model_ids",
        "created_at",
        "updated_at",
    }
    assert response.json()["must_change_password"] is False
    assert "password_hash" not in response.text.lower()

    # The target user's existing session is revoked; the new password works immediately.
    assert user.get("/api/v1/session").status_code == 401
    login = user.post(
        "/api/v1/auth/login",
        json={"username": accounts.user_username, "password": "admin-issued-correct-horse"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is False

    events = app.state.canvas_store.admin_audit_events()
    assert any(
        event["action"] == "set_password"
        and event["actor_user_id"] == accounts.admin.user_id
        and event["target_type"] == "user"
        and event["target_id"] == accounts.user.user_id
        for event in events
    )


def test_admin_set_password_can_force_change_on_next_login(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, user_headers
    assert accounts.user is not None

    response = admin.post(
        f"/api/v1/admin/users/{accounts.user.user_id}/password",
        headers=admin_headers,
        json={"new_password": "forced-change-correct-horse", "must_change_password": True},
    )

    assert response.status_code == 200
    assert response.json()["must_change_password"] is True

    login = user.post(
        "/api/v1/auth/login",
        json={"username": accounts.user_username, "password": "forced-change-correct-horse"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True
    csrf = login.json()["csrf_token"]

    changed = user.post(
        "/api/v1/auth/change-password",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json={"current_password": "forced-change-correct-horse", "new_password": "finally-their-own-password"},
    )
    assert changed.status_code == 200
    assert changed.json()["user"]["must_change_password"] is False


def test_admin_set_password_hides_admin_unknown_and_pending_targets(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, user, user_headers
    assert accounts.admin is not None

    body = {"new_password": "admin-issued-correct-horse"}

    assert admin.post(
        f"/api/v1/admin/users/{accounts.admin.user_id}/password",
        headers=admin_headers,
        json=body,
    ).status_code == 404
    assert admin.post(
        "/api/v1/admin/users/no-such-user/password",
        headers=admin_headers,
        json=body,
    ).status_code == 404

    admin.post(
        "/api/v1/auth/register",
        json={"username": "still-pending", "display_name": "待审核", "password": "correct-horse-battery"},
    )
    pending_id = next(
        item["user_id"]
        for item in admin.get("/api/v1/admin/registrations", headers=admin_headers).json()["registrations"]
        if item["username"] == "still-pending"
    )
    assert admin.post(
        f"/api/v1/admin/users/{pending_id}/password",
        headers=admin_headers,
        json=body,
    ).status_code == 404


def test_admin_set_password_is_admin_only_and_validates_body(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app
    assert accounts.user is not None
    user_id = accounts.user.user_id
    del accounts

    assert user.post(
        f"/api/v1/admin/users/{user_id}/password",
        headers=user_headers,
        json={"new_password": "admin-issued-correct-horse"},
    ).status_code == 404

    assert admin.post(
        f"/api/v1/admin/users/{user_id}/password",
        headers=admin_headers,
        json={"new_password": "too-short"},
    ).status_code == 400
    assert admin.post(
        f"/api/v1/admin/users/{user_id}/password",
        headers=admin_headers,
        json={"new_password": "admin-issued-correct-horse", "extra": 1},
    ).status_code == 400


def test_admin_can_set_password_for_a_disabled_user(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, user_headers
    assert accounts.user is not None

    assert admin.patch(
        f"/api/v1/admin/users/{accounts.user.user_id}",
        headers=admin_headers,
        json={"enabled": False},
    ).status_code == 200

    response = admin.post(
        f"/api/v1/admin/users/{accounts.user.user_id}/password",
        headers=admin_headers,
        json={"new_password": "disabled-user-correct-horse"},
    )
    assert response.status_code == 200

    assert admin.patch(
        f"/api/v1/admin/users/{accounts.user.user_id}",
        headers=admin_headers,
        json={"enabled": True},
    ).status_code == 200
    assert user.post(
        "/api/v1/auth/login",
        json={"username": accounts.user_username, "password": "disabled-user-correct-horse"},
    ).status_code == 200


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


def test_admin_lists_approves_and_rejects_registrations(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, user_headers

    register = admin.post(
        "/api/v1/auth/register",
        json={"username": "keeper", "display_name": "保留者", "password": "correct-horse-battery"},
    )
    assert register.status_code == 201

    pending = admin.get("/api/v1/admin/registrations", headers=admin_headers)
    assert pending.status_code == 200
    registrations = pending.json()["registrations"]
    assert len(registrations) == 1
    assert set(registrations[0]) == {"user_id", "username", "display_name", "created_at"}
    assert registrations[0]["username"] == "keeper"
    assert "password" not in pending.text.lower()

    approved = admin.post(
        f"/api/v1/admin/registrations/{registrations[0]['user_id']}/approve",
        headers=admin_headers,
    )
    assert approved.status_code == 200
    assert approved.json()["enabled"] is True
    assert approved.json()["approval_status"] == "approved"

    users = admin.get("/api/v1/admin/users", headers=admin_headers).json()["users"]
    keeper = next(item for item in users if item["username"] == "keeper")
    assert keeper["approval_status"] == "approved"
    assert keeper["enabled"] is True
    assert keeper["model_ids"] == []

    admin.post(
        "/api/v1/auth/register",
        json={"username": "dropped", "display_name": "被拒绝者", "password": "correct-horse-battery"},
    )
    dropped = next(
        item for item in admin.get("/api/v1/admin/registrations", headers=admin_headers).json()["registrations"]
        if item["username"] == "dropped"
    )
    rejected = admin.post(
        f"/api/v1/admin/registrations/{dropped['user_id']}/reject",
        headers=admin_headers,
    )
    assert rejected.status_code == 204

    assert admin.get("/api/v1/admin/registrations", headers=admin_headers).json()["registrations"] == []
    remaining = admin.get("/api/v1/admin/users", headers=admin_headers).json()["users"]
    assert all(item["username"] != "dropped" for item in remaining)
    dropped_login = admin.post(
        "/api/v1/auth/login",
        json={"username": "dropped", "password": "correct-horse-battery"},
    )
    assert dropped_login.status_code == 401

    # The approved user can sign in; use a separate client so the admin session stays intact.
    assert user.post(
        "/api/v1/auth/login",
        json={"username": "keeper", "password": "correct-horse-battery"},
    ).status_code == 200


def test_admin_registration_endpoints_are_admin_only(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, admin_headers

    admin.post(
        "/api/v1/auth/register",
        json={"username": "newcomer", "display_name": "新同事", "password": "correct-horse-battery"},
    )
    assert user.get("/api/v1/admin/registrations", headers=user_headers).status_code == 404
    assert user.post(
        "/api/v1/admin/registrations/any-user-id/approve",
        headers=user_headers,
    ).status_code == 404
    assert user.post(
        "/api/v1/admin/registrations/any-user-id/reject",
        headers=user_headers,
    ).status_code == 404


def test_admin_usage_rates_and_cost_projection_are_protected(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    assert accounts.user is not None
    assert user.get("/api/v1/admin/usage").status_code == 404
    assert user.put(
        "/api/v1/admin/usage/rates",
        headers=user_headers,
        json={"video_price_fen": 1, "image_price_fen": 2},
    ).status_code == 404
    assert admin.put(
        "/api/v1/admin/usage/rates",
        headers=admin_headers,
        json={"video_price_fen": True, "image_price_fen": 2, "extra": 3},
    ).status_code == 400

    response = admin.put(
        "/api/v1/admin/usage/rates",
        headers=admin_headers,
        json={"video_price_fen": 25, "image_price_fen": 120},
    )
    assert response.status_code == 200
    assert response.json() == {"video_price_fen": 25, "image_price_fen": 120}

    reserved = app.state.canvas_store.reserve_job(
        user_id=accounts.user.user_id,
        job_id="charged-image",
        service_id="image",
        operation="image.generate",
        idempotency_key="charged-image-key",
        request_hash="charged-image-hash",
        model_id="banana",
        image_count=1,
    )
    app.state.canvas_store.mark_submitted(
        "charged-image",
        "upstream-image",
        "succeeded",
        str(reserved.job["submission_token"]),
        result_ids=("charged-result",),
    )
    usage = admin.get("/api/v1/admin/usage").json()
    assert usage["summary"]["total_cost_fen"] == "120"
    assert usage["jobs"] == [
        {
            "user_id": accounts.user.user_id,
            "operation": "image.generate",
            "status": "succeeded",
            "model_id": "banana",
            "route_id": None,
            "video_seconds": 0,
            "image_count": 1,
            "video_price_fen": "25",
            "image_price_fen": "120",
            "cost_fen": "120",
            "charged_at": usage["jobs"][0]["charged_at"],
        }
    ]

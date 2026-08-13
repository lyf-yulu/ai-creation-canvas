from __future__ import annotations

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


def test_only_admin_can_read_and_change_usage_rates(tmp_path):
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts
    assert user.get("/api/v1/admin/usage").status_code == 404
    assert user.put("/api/v1/admin/usage/rates", headers=user_headers, json={"video_price_fen": 1, "image_price_fen": 2}).status_code == 404
    assert user.put("/api/v1/admin/usage/rates", headers=user_headers, json={"video_price_fen": True, "image_price_fen": 2, "extra": 3}).status_code == 404
    assert admin.put("/api/v1/admin/usage/rates", headers=admin_headers, json={"video_price_fen": True, "image_price_fen": 2, "extra": 3}).status_code == 400
    response = admin.put("/api/v1/admin/usage/rates", headers=admin_headers, json={"video_price_fen": 25, "image_price_fen": 120})
    assert response.status_code == 200
    assert response.json() == {"video_price_fen": 25, "image_price_fen": 120}


def test_admin_usage_returns_global_summary_users_and_jobs(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del user, admin_headers, user_headers
    assert accounts.admin is not None
    assert accounts.user is not None
    store = app.state.canvas_store
    store.set_usage_rates(video_price_fen=25, image_price_fen=120)
    image = store.reserve_job(
        user_id=accounts.user.user_id,
        job_id="image",
        service_id="images",
        operation="image.generate",
        idempotency_key="image-key",
        request_hash="image-hash",
        image_count=1,
    )
    video = store.reserve_job(
        user_id=accounts.admin.user_id,
        job_id="video",
        service_id="videos",
        operation="video.generate",
        idempotency_key="video-key",
        request_hash="video-hash",
        video_seconds=5,
    )
    store.mark_submitted("image", "upstream-image", "succeeded", str(image.job["submission_token"]))
    store.mark_submitted("video", "upstream-video", "succeeded", str(video.job["submission_token"]))

    response = admin.get("/api/v1/admin/usage")

    assert response.status_code == 200
    assert response.json()["summary"] == {
        "successful_jobs": 2,
        "image_count": 1,
        "video_seconds": 5,
        "total_cost_fen": 245,
    }
    assert {item["user_id"] for item in response.json()["users"]} == {accounts.admin.user_id, accounts.user.user_id}
    assert {item["user_id"] for item in response.json()["jobs"]} == {accounts.admin.user_id, accounts.user.user_id}
    assert "request_hash" not in response.text

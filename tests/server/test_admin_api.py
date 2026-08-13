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
    response = admin.put("/api/v1/admin/usage/rates", headers=admin_headers, json={"video_price_fen": 25, "image_price_fen": 120})
    assert response.status_code == 200
    assert response.json() == {"video_price_fen": 25, "image_price_fen": 120}

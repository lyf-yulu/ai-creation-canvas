from __future__ import annotations

from fastapi.testclient import TestClient

from ai_creation_canvas.__main__ import create_local_app
from ai_creation_canvas.domain.models import PortalRole
from tests.server.test_model_assignments import ORIGIN
from tests.server.test_projects_api import project_body


def login_and_change(client: TestClient, username: str, password: str) -> dict[str, str]:
    logged_in = client.post("/api/v1/auth/login", json={"username": username, "password": password}).json()
    changed = client.post(
        "/api/v1/auth/change-password",
        headers={"Origin": ORIGIN, "X-CSRF-Token": logged_in["csrf_token"]},
        json={"current_password": password, "new_password": "new-correct-horse-battery"},
    )
    assert changed.status_code == 200
    return {"Origin": ORIGIN, "X-CSRF-Token": changed.json()["csrf_token"]}


def test_initial_password_session_cannot_use_product_apis_until_password_changes(tmp_path) -> None:
    app, accounts = create_local_app(
        port=8992,
        data_dir=tmp_path / "local-data",
        static_dir=tmp_path / "dist",
        bootstrap_if_empty=True,
    )
    assert accounts is not None and accounts.created
    client = TestClient(app, base_url=ORIGIN)
    logged_in = client.post(
        "/api/v1/auth/login",
        json={"username": accounts.user_username, "password": accounts.user_password},
    ).json()
    headers = {"Origin": ORIGIN, "X-CSRF-Token": logged_in["csrf_token"]}

    assert client.get("/api/v1/session").status_code == 200
    blocked_models = client.get("/api/v1/models")
    blocked_project = client.post(
        "/api/v1/projects",
        headers=headers,
        json=project_body("blocked-before-password-change", "不应创建"),
    )
    for response in (blocked_models, blocked_project):
        assert response.status_code == 403
        assert response.json()["code"] == "PASSWORD_CHANGE_REQUIRED"

    changed = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": accounts.user_password, "new_password": "new-correct-horse-battery"},
    )
    assert changed.status_code == 200
    assert client.get("/api/v1/models").status_code == 200


def test_slice1_admin_user_project_assignment_and_demo_result(tmp_path) -> None:
    app, accounts = create_local_app(
        port=8992,
        data_dir=tmp_path / "local-data",
        static_dir=tmp_path / "dist",
        bootstrap_if_empty=True,
    )
    assert accounts is not None and accounts.created and accounts.user is not None
    admin = TestClient(app, base_url=ORIGIN)
    user = TestClient(app, base_url=ORIGIN)
    admin_headers = login_and_change(admin, accounts.admin_username, accounts.admin_password)
    user_headers = login_and_change(user, accounts.user_username, accounts.user_password)

    assert admin.get("/api/v1/admin/users").status_code == 200
    assert user.get("/api/v1/admin/users").status_code == 404
    assert [model["model_id"] for model in user.get("/api/v1/models").json()["models"]] == ["demo-image-v1"]

    project = user.post("/api/v1/projects", headers=user_headers, json=project_body("local-1", "首个项目"))
    assert project.status_code == 201
    payload = {
        "operation": "image.generate", "model_id": "demo-image-v1", "prompt": "slice one acceptance",
        "params": {"aspect_ratio": "landscape"}, "asset_ids": [], "idempotency_key": "slice-one-demo",
    }
    created = user.post("/api/v1/jobs", headers=user_headers, json=payload)
    assert created.status_code == 201
    done = user.get(f"/api/v1/jobs/{created.json()['id']}")
    assert done.json()["status"] == "succeeded"
    assert user.get(done.json()["result_url"]).headers["content-type"] == "image/png"
    assert user.get("/api/v1/projects").json()["projects"][0]["project"]["title"] == "首个项目"

    second_record = app.state.local_auth.create_user("slice-user-b", "用户 B", "correct-horse-battery", PortalRole.USER, must_change_password=False)
    second = TestClient(app, base_url=ORIGIN)
    second_login = second.post("/api/v1/auth/login", json={"username": "slice-user-b", "password": "correct-horse-battery"}).json()
    second_headers = {"Origin": ORIGIN, "X-CSRF-Token": second_login["csrf_token"]}
    assert second.get("/api/v1/projects/local-1").status_code == 404
    assert second.get(f"/api/v1/jobs/{created.json()['id']}").status_code == 404
    assert second.get(done.json()["result_url"]).status_code == 404
    own = second.post("/api/v1/projects", headers=second_headers, json=project_body("local-b", "B 项目"))
    assert own.status_code == 201
    assert second_record.user_id not in user.get("/api/v1/projects").text

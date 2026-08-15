from __future__ import annotations

from fastapi.testclient import TestClient

from ai_creation_canvas.auth.passwords import PasswordHasher
from tests.server.test_comfy_workflow_api import _configure_service, _import
from tests.server.test_model_assignments import ORIGIN, local_clients


def test_assigned_user_can_only_read_safe_metadata_while_other_users_are_hidden(tmp_path) -> None:
    app, accounts, admin, user_a, admin_headers, user_a_headers = local_clients(tmp_path)
    assert accounts.user is not None
    _configure_service(app)
    app.state.canvas_store.create_user(
        user_id="workflow-user-b",
        username_normalized="workflow-user-b",
        display_name="Workflow User B",
        password_hash=PasswordHasher.hash("correct-horse-user-b"),
        role="user",
        must_change_password=False,
    )
    user_b = TestClient(app, base_url=ORIGIN)
    login = user_b.post("/api/v1/auth/login", json={"username": "workflow-user-b", "password": "correct-horse-user-b"})
    assert login.status_code == 200

    workflow_id = _import(admin, admin_headers)
    assert admin.put(
        f"/api/v1/admin/users/{accounts.user.user_id}/comfy-workflows",
        headers=admin_headers,
        json={"workflow_ids": [workflow_id]},
    ).status_code == 200
    assert admin.post(
        f"/api/v1/admin/comfy-workflows/{workflow_id}/enable",
        headers=admin_headers,
        json={"revision": 1},
    ).status_code == 200

    detail = user_a.get(f"/api/v1/comfy-workflows/{workflow_id}")
    assert detail.status_code == 200
    for client in (user_a, user_b):
        assert client.get(f"/api/v1/comfy-workflows/{workflow_id}/revisions/1/preview").status_code == 404
        assert client.get(f"/api/v1/comfy-workflows/{workflow_id}/revisions/1/export?format=editor").status_code == 404
    assert detail.json()["revision"] == 1
    assert detail.json()["lifecycle_revision"] == 2
    assert "widgets_values" not in detail.text
    assert "checksum_prefix" not in detail.text
    assert user_b.get(f"/api/v1/comfy-workflows/{workflow_id}").status_code == 404
    assert user_a.post(
        f"/api/v1/admin/comfy-workflows/{workflow_id}/disable",
        headers=user_a_headers,
        json={"revision": 2},
    ).status_code == 404

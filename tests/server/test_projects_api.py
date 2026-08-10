from __future__ import annotations

import json

from fastapi.testclient import TestClient

from ai_creation_canvas.domain.models import PortalRole
from tests.server.test_model_assignments import ORIGIN, local_clients


def project_body(project_id: str, title: str) -> dict[str, object]:
    return {
        "id": project_id,
        "title": title,
        "createdAt": "2026-08-10T00:00:00.000Z",
        "updatedAt": "2026-08-10T00:00:00.000Z",
        "nodes": [],
        "connections": [],
        "chatSessions": [],
        "activeChatId": None,
        "backgroundMode": "lines",
        "showImageInfo": False,
        "viewport": {"x": 0, "y": 0, "k": 1},
    }


def project_clients(tmp_path):
    app, accounts, admin, user_a, admin_headers, user_a_headers = local_clients(tmp_path)
    user_b_record = app.state.local_auth.create_user(
        "canvas-user-b",
        "普通用户 B",
        "correct-horse-battery",
        PortalRole.USER,
        must_change_password=False,
    )
    user_b = TestClient(app, base_url=ORIGIN)
    login = user_b.post("/api/v1/auth/login", json={"username": "canvas-user-b", "password": "correct-horse-battery"}).json()
    user_b_headers = {"Origin": ORIGIN, "X-CSRF-Token": login["csrf_token"]}
    assert accounts.user is not None
    return app, user_a, user_a_headers, accounts.user.user_id, user_b, user_b_headers, user_b_record.user_id


def test_projects_are_owned_listed_and_versioned(tmp_path) -> None:
    app, user_a, headers_a, owner_a, user_b, headers_b, owner_b = project_clients(tmp_path)
    del app, headers_b, owner_b

    created = user_a.post("/api/v1/projects", headers=headers_a, json=project_body("p-1", "A"))
    assert created.status_code == 201
    assert created.json()["version"] == 1
    assert created.json()["project"]["title"] == "A"
    assert [item["project"]["id"] for item in user_a.get("/api/v1/projects").json()["projects"]] == ["p-1"]
    assert user_b.get("/api/v1/projects/p-1").status_code == 404

    version = created.json()["version"]
    updated = user_a.put("/api/v1/projects/p-1", headers=headers_a, json={**project_body("p-1", "B"), "expected_version": version})
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["project"]["title"] == "B"

    conflict = user_a.put("/api/v1/projects/p-1", headers=headers_a, json={**project_body("p-1", "C"), "expected_version": version})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "PROJECT_CONFLICT"
    assert user_a.get("/api/v1/projects/p-1").json()["project"]["title"] == "B"

    deleted = user_a.delete("/api/v1/projects/p-1", headers=headers_a)
    assert deleted.status_code == 204
    assert user_a.get("/api/v1/projects/p-1").status_code == 404
    assert owner_a not in deleted.text


def test_project_document_bounds_and_shape_are_rejected(tmp_path) -> None:
    app, user, headers, owner, user_b, headers_b, owner_b = project_clients(tmp_path)
    del app, owner, user_b, headers_b, owner_b

    invalid_documents: list[dict[str, object]] = []
    invalid_documents.append({**project_body("unknown", "Unknown"), "unexpected": True})
    invalid_documents.append({**project_body("too-many-nodes", "Nodes"), "nodes": [{}] * 1001})
    invalid_documents.append({**project_body("too-many-connections", "Connections"), "connections": [{}] * 2001})
    invalid_documents.append({**project_body("too-large", "Large"), "nodes": [{"payload": "x" * (1024 * 1024)}]})
    nested: object = "leaf"
    for _ in range(34):
        nested = {"child": nested}
    invalid_documents.append({**project_body("too-deep", "Deep"), "nodes": [{"payload": nested}]})

    for body in invalid_documents:
        response = user.post("/api/v1/projects", headers=headers, json=body)
        assert response.status_code == 400, body["id"]
        assert response.json()["code"] == "REQUEST_REJECTED"

    mismatch = user.put("/api/v1/projects/path-id", headers=headers, json={**project_body("body-id", "Mismatch"), "expected_version": 1})
    assert mismatch.status_code == 400

    non_finite = project_body("non-finite", "NaN")
    non_finite["viewport"] = {"x": float("nan"), "y": 0, "k": 1}
    raw = json.dumps(non_finite, allow_nan=True).encode()
    response = user.post("/api/v1/projects", headers={**headers, "Content-Type": "application/json"}, content=raw)
    assert response.status_code == 400


def test_project_create_is_idempotent_only_for_the_same_document(tmp_path) -> None:
    app, user, headers, owner, user_b, headers_b, owner_b = project_clients(tmp_path)
    del app, owner, user_b, headers_b, owner_b

    first = user.post("/api/v1/projects", headers=headers, json=project_body("same-id", "A"))
    repeated = user.post("/api/v1/projects", headers=headers, json=project_body("same-id", "A"))
    different = user.post("/api/v1/projects", headers=headers, json=project_body("same-id", "B"))

    assert first.status_code == 201
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    assert different.status_code == 409

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tests.server.test_model_assignments import ORIGIN, local_clients


FIXTURE = Path(__file__).parents[1] / "fixtures" / "comfy" / "core-load-save-workflow.json"


def _configure_service(app) -> None:
    app.state.comfy_workflow_services = (SimpleNamespace(service_id="comfy-local"),)


def _import(admin: TestClient, headers: dict[str, str]) -> str:
    response = admin.post(
        "/api/v1/admin/comfy-workflows/import",
        headers=headers,
        files={"file": ("core.json", FIXTURE.read_bytes(), "application/json")},
        data={"display_name": "Core", "service_id": "comfy-local"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["workflow_id"])


def test_admin_imports_exports_and_assigns_without_exposing_raw_api_json(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    assert accounts.user is not None
    _configure_service(app)

    workflow_id = _import(admin, admin_headers)
    imported = admin.get(f"/api/v1/admin/comfy-workflows/{workflow_id}")

    assert "widgets_values" not in imported.text
    exported = admin.get(f"/api/v1/admin/comfy-workflows/{workflow_id}/revisions/1/export?format=editor")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/json")
    assert exported.headers["content-disposition"] == (
        f'attachment; filename="comfy-workflow-{workflow_id}-r1-editor.json"'
    )
    assert user.get("/api/v1/comfy-workflows").json()["workflows"] == []

    assigned = admin.put(
        f"/api/v1/admin/users/{accounts.user.user_id}/comfy-workflows",
        headers=admin_headers,
        json={"workflow_ids": [workflow_id]},
    )
    assert assigned.status_code == 200
    assert user.get("/api/v1/comfy-workflows").json()["workflows"] == []

    enabled = admin.post(
        f"/api/v1/admin/comfy-workflows/{workflow_id}/enable",
        headers=admin_headers,
        json={"revision": 1},
    )
    assert enabled.status_code == 200
    assert [item["workflow_id"] for item in user.get("/api/v1/comfy-workflows").json()["workflows"]] == [workflow_id]
    assert user.get(f"/api/v1/comfy-workflows/{workflow_id}/revisions/1/preview").status_code == 200


def test_workflow_routes_enforce_rbac_csrf_and_strict_multipart(tmp_path) -> None:
    _app, _accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)

    assert user.get("/api/v1/admin/comfy-workflows").status_code == 404
    assert user.post(
        "/api/v1/admin/comfy-workflows/import",
        headers=user_headers,
        files={"file": ("core.json", FIXTURE.read_bytes(), "application/json")},
        data={"display_name": "Core", "service_id": "comfy-local"},
    ).status_code == 404
    assert admin.post(
        "/api/v1/admin/comfy-workflows/import",
        files={"file": ("core.json", FIXTURE.read_bytes(), "application/json")},
        data={"display_name": "Core", "service_id": "comfy-local"},
    ).status_code == 403
    assert admin.post(
        "/api/v1/admin/comfy-workflows/import",
        headers=admin_headers,
        files={"file": ("core.txt", b"{}", "text/plain")},
        data={"display_name": "Core", "service_id": "comfy-local"},
    ).status_code == 400
    assert admin.post(
        "/api/v1/admin/comfy-workflows/import",
        headers=admin_headers,
        files=[("file", ("one.json", b"{}", "application/json")), ("file", ("two.json", b"{}", "application/json"))],
        data={"display_name": "Core", "service_id": "comfy-local"},
    ).status_code == 400
    assert admin.post(
        "/api/v1/admin/comfy-workflows/import",
        headers=admin_headers,
        files={"file": ("core.json", FIXTURE.read_bytes(), "application/json")},
        data={"display_name": "Core", "service_id": "comfy-local", "unexpected": "no"},
    ).status_code == 400
    workflow_id = _import(admin, admin_headers)
    unavailable = admin.post(
        f"/api/v1/admin/comfy-workflows/{workflow_id}/enable",
        headers=admin_headers,
        json={"revision": 1},
    )
    assert unavailable.status_code == 400
    assert unavailable.json()["code"] == "WORKFLOW_SERVICE_UNAVAILABLE"


def test_admin_lifecycle_and_revision_routes_are_optimistic_and_safe(tmp_path) -> None:
    app, _accounts, admin, _user, headers, _user_headers = local_clients(tmp_path)
    _configure_service(app)
    workflow_id = _import(admin, headers)

    assert admin.post(
        f"/api/v1/admin/comfy-workflows/{workflow_id}/enable",
        headers=headers,
        json={"revision": True},
    ).status_code == 400
    assert admin.post(
        f"/api/v1/admin/comfy-workflows/{workflow_id}/revisions",
        headers=headers,
        files={"file": ("core.json", FIXTURE.read_bytes(), "application/json")},
        data={"revision": "1"},
    ).status_code == 400
    assert admin.post(
        f"/api/v1/admin/comfy-workflows/{workflow_id}/enable",
        headers=headers,
        json={"revision": 1},
    ).status_code == 200
    assert admin.post(
        f"/api/v1/admin/comfy-workflows/{workflow_id}/revisions",
        headers=headers,
        files={"file": ("next.json", b'{"1":{"class_type":"LoadImage","inputs":{}}}', "application/json")},
        data={"revision": "2"},
    ).status_code == 400
    disabled = admin.post(
        f"/api/v1/admin/comfy-workflows/{workflow_id}/disable",
        headers=headers,
        json={"revision": 2},
    )
    assert disabled.status_code == 200
    created = admin.post(
        f"/api/v1/admin/comfy-workflows/{workflow_id}/revisions",
        headers=headers,
        files={"file": ("next.json", b'{"1":{"class_type":"LoadImage","inputs":{}}}', "application/json")},
        data={"revision": "3"},
    )
    assert created.status_code == 201
    assert created.json()["revision"] == 4

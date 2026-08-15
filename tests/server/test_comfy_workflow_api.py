from __future__ import annotations

import json
from pathlib import Path
from tempfile import SpooledTemporaryFile
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from starlette.datastructures import Headers, UploadFile

from ai_creation_canvas.api import comfy_workflows
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
    assert user.get(f"/api/v1/comfy-workflows/{workflow_id}/revisions/1/preview").status_code == 404


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


@pytest.mark.parametrize(
    "field",
    (
        "auth_header_ref", "base_url", "callback_url", "webhook_url", "endpoint", "base endpoint", "webhook\tendpoint", "server.endpoint", "service endpoint url", "service-url-endpoint", "callback endpoint url", "callback_url_endpoint", "resource_endpoint", "ScRiPt", "service_url", "apiKey", "API-KEY", "password", "secret_ref",
        "api key", "auth\ttoken", "credential.ref", "service u r l", "endpoint\nu-r_l", "s.c.r.i.p.t",
        " api_key ", "\tSeCrEt\n", "\tcredential\n",
    ),
)
def test_admin_import_rejects_recursive_sensitive_workflow_fields_before_persistence(tmp_path, field: str) -> None:
    app, _accounts, admin, _user, headers, _user_headers = local_clients(tmp_path)
    secret = "server-only-workflow-secret"
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["extra"][field] = secret

    response = admin.post(
        "/api/v1/admin/comfy-workflows/import",
        headers=headers,
        files={"file": ("unsafe.json", json.dumps(payload).encode("utf-8"), "application/json")},
        data={"display_name": "Unsafe", "service_id": "comfy-local"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "WORKFLOW_FIELD_REJECTED"
    assert secret not in response.text
    assert app.state.comfy_workflow_library.admin_list() == ()


def test_admin_import_allows_resource_url_metadata_without_projecting_its_value(tmp_path) -> None:
    app, _accounts, admin, _user, headers, _user_headers = local_clients(tmp_path)
    url = "https://workflow.example/metadata"
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["extra"]["CoS-URL"] = url

    response = admin.post(
        "/api/v1/admin/comfy-workflows/import",
        headers=headers,
        files={"file": ("url.json", json.dumps(payload).encode("utf-8"), "application/json")},
        data={"display_name": "URL workflow", "service_id": "comfy-local"},
    )

    assert response.status_code == 201
    workflow_id = str(response.json()["workflow_id"])
    preview = admin.get(f"/api/v1/admin/comfy-workflows/{workflow_id}/revisions/1/preview")
    assert preview.status_code == 200
    assert url not in preview.text


def test_admin_import_accepts_normal_workflow_fixture(tmp_path) -> None:
    app, _accounts, admin, _user, headers, _user_headers = local_clients(tmp_path)

    workflow_id = _import(admin, headers)

    assert [item.workflow_id for item in app.state.comfy_workflow_library.admin_list()] == [workflow_id]


def test_admin_workflow_metadata_exposes_checksum_prefix_and_assignments_without_raw_data(tmp_path) -> None:
    app, accounts, admin, user, headers, _user_headers = local_clients(tmp_path)
    assert accounts.user is not None
    workflow_id = _import(admin, headers)
    assert admin.put(
        f"/api/v1/admin/users/{accounts.user.user_id}/comfy-workflows",
        headers=headers,
        json={"workflow_ids": [workflow_id]},
    ).status_code == 200

    listed = admin.get("/api/v1/admin/comfy-workflows")
    detail = admin.get(f"/api/v1/admin/comfy-workflows/{workflow_id}")
    preview = admin.get(f"/api/v1/admin/comfy-workflows/{workflow_id}/revisions/1/preview")
    users = admin.get("/api/v1/admin/users")

    assert listed.status_code == detail.status_code == preview.status_code == users.status_code == 200
    expected_prefix = "bd97659461bf"
    assert listed.json()["workflows"][0]["checksum_prefix"] == expected_prefix
    assert detail.json()["checksum_prefix"] == expected_prefix
    assert preview.json()["checksum_prefix"] == expected_prefix
    by_id = {item["user_id"]: item for item in users.json()["users"]}
    assert by_id[accounts.user.user_id]["comfy_workflow_ids"] == [workflow_id]
    assert "widgets_values" not in f"{listed.text}{detail.text}{preview.text}{users.text}"
    assert user.get("/api/v1/admin/comfy-workflows").status_code == 404
    assert user.get("/api/v1/admin/users").status_code == 404


def test_workflow_projection_uses_document_revision_not_lifecycle_revision(tmp_path) -> None:
    app, _accounts, admin, _user, headers, _user_headers = local_clients(tmp_path)
    _configure_service(app)
    workflow_id = _import(admin, headers)

    imported = admin.get(f"/api/v1/admin/comfy-workflows/{workflow_id}").json()
    assert (imported["revision"], imported["lifecycle_revision"]) == (1, 1)
    enabled = admin.post(
        f"/api/v1/admin/comfy-workflows/{workflow_id}/enable", headers=headers, json={"revision": 1}
    ).json()
    assert (enabled["revision"], enabled["lifecycle_revision"]) == (1, 2)
    assert admin.get(f"/api/v1/admin/comfy-workflows/{workflow_id}/revisions/1/preview").status_code == 200
    assert admin.get(f"/api/v1/admin/comfy-workflows/{workflow_id}/revisions/1/export?format=editor").status_code == 200

    def assert_document_routes() -> None:
        detail = admin.get(f"/api/v1/admin/comfy-workflows/{workflow_id}")
        assert detail.json()["revision"] == 1
        assert detail.json()["current_revision"]["revision"] == 1
        assert admin.get(f"/api/v1/admin/comfy-workflows/{workflow_id}/revisions/1/preview").status_code == 200
        assert admin.get(f"/api/v1/admin/comfy-workflows/{workflow_id}/revisions/1/export?format=editor").status_code == 200

    disabled = admin.post(
        f"/api/v1/admin/comfy-workflows/{workflow_id}/disable", headers=headers, json={"revision": 2}
    ).json()
    assert_document_routes()
    archived = admin.post(
        f"/api/v1/admin/comfy-workflows/{workflow_id}/archive", headers=headers, json={"revision": 3}
    ).json()
    assert_document_routes()
    restored = admin.post(
        f"/api/v1/admin/comfy-workflows/{workflow_id}/restore", headers=headers, json={"revision": 4}
    ).json()
    assert_document_routes()
    assert (disabled["revision"], disabled["lifecycle_revision"]) == (1, 3)
    assert (archived["revision"], archived["lifecycle_revision"]) == (1, 4)
    assert (restored["revision"], restored["lifecycle_revision"]) == (1, 5)


def test_invalid_revision_field_closes_parsed_upload(tmp_path, monkeypatch) -> None:
    _app, _accounts, admin, _user, headers, _user_headers = local_clients(tmp_path)
    workflow_id = _import(admin, headers)
    temporary = SpooledTemporaryFile()
    temporary.write(b"{}")
    upload = UploadFile(file=temporary, filename="workflow.json", headers=Headers({"content-type": "application/json"}))

    async def parsed_form(*_args, **_kwargs):
        return comfy_workflows._WorkflowForm(upload, {"revision": "not-an-integer"})

    monkeypatch.setattr(comfy_workflows, "_single_workflow_form", parsed_form)
    response = admin.post(
        f"/api/v1/admin/comfy-workflows/{workflow_id}/revisions",
        headers=headers,
        content=b"ignored-by-patched-parser",
    )

    assert response.status_code == 400
    assert temporary.closed


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
    assert created.json()["lifecycle_revision"] == 4

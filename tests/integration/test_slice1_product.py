from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from ai_creation_canvas.__main__ import create_local_app
from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import AssetRef, JobRequest, JobState, ModelSpec, PortalRole, RequestContext, UpstreamJob
from ai_creation_canvas.domain.registry import AdapterRegistry
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


class ImmediateSuccessCostAdapter:
    """Offline image/video adapter used to exercise the public job completion path."""

    service_id = "cost-flow-fixture"

    async def list_models(self, context: RequestContext) -> tuple[ModelSpec, ...]:
        del context
        return (
            ModelSpec("cost-image-v1", self.service_id, "测试图片", ("image.generate",), ("text",), {}),
            ModelSpec(
                "cost-video-v1",
                self.service_id,
                "测试视频",
                ("video.generate",),
                ("text",),
                {
                    "type": "object",
                    "properties": {"duration": {"type": "integer", "minimum": 5, "maximum": 5}},
                    "required": ["duration"],
                    "additionalProperties": False,
                },
            ),
        )

    async def submit(self, context: RequestContext, request: JobRequest) -> UpstreamJob:
        upstream_id = f"completed-{request.idempotency_key}"
        result = AssetRef(f"result-{request.idempotency_key}", "reference", "active", "image/png")
        return UpstreamJob(self.service_id, upstream_id, JobState(upstream_id, "succeeded", result), datetime.now(UTC))

    async def poll(self, context: RequestContext, upstream_job_id: str) -> JobState:
        del context
        return JobState(upstream_job_id, "succeeded", AssetRef(f"result-{upstream_job_id}", "reference", "active", "image/png"))


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


def test_local_admin_price_freeze_is_visible_only_to_the_owner_and_admin(tmp_path) -> None:
    registry = AdapterRegistry()
    registry.register_generation(ImmediateSuccessCostAdapter())
    settings = Settings(
        "test",
        8992,
        tmp_path / "data",
        "test-secret",
        identity_mode="local",
        allowed_origins=(ORIGIN,),
    )
    app = create_app(
        settings,
        static_dir=tmp_path / "dist",
        registry=registry,
        model_catalog=ModelCatalog(registry),
    )
    accounts = app.state.local_auth.bootstrap_accounts(("cost-image-v1", "cost-video-v1"))
    assert accounts.user is not None
    admin = TestClient(app, base_url=ORIGIN)
    user = TestClient(app, base_url=ORIGIN)
    admin_headers = login_and_change(admin, accounts.admin_username, accounts.admin_password)
    user_headers = login_and_change(user, accounts.user_username, accounts.user_password)

    def complete(model_id: str, operation: str, *, params: dict[str, object], key: str) -> None:
        response = user.post(
            "/api/v1/jobs",
            headers=user_headers,
            json={
                "operation": operation,
                "model_id": model_id,
                "prompt": "cost acceptance",
                "params": params,
                "asset_ids": [],
                "idempotency_key": key,
            },
        )
        assert response.status_code == 201
        assert response.json()["status"] == "succeeded"

    initial_rates = admin.put(
        "/api/v1/admin/usage/rates",
        headers=admin_headers,
        json={"image_price_fen": 120, "video_price_fen": 25},
    )
    assert initial_rates.status_code == 200
    complete("cost-image-v1", "image.generate", params={}, key="first-image")
    complete("cost-video-v1", "video.generate", params={"duration": 5}, key="first-video")

    changed_rates = admin.put(
        "/api/v1/admin/usage/rates",
        headers=admin_headers,
        json={"image_price_fen": 999, "video_price_fen": 99},
    )
    assert changed_rates.status_code == 200
    complete("cost-image-v1", "image.generate", params={}, key="second-image")

    owner_usage = user.get("/api/v1/usage")
    assert owner_usage.status_code == 200
    charged_jobs = owner_usage.json()["jobs"]
    assert len(charged_jobs) == 3
    video_job = next(item for item in charged_jobs if item["operation"] == "video.generate")
    first_image_job = next(item for item in charged_jobs if item["operation"] == "image.generate" and item["image_price_fen"] == "120")
    later_image_job = next(item for item in charged_jobs if item["operation"] == "image.generate" and item["image_price_fen"] == "999")
    assert video_job["operation"] == "video.generate"
    assert video_job["video_seconds"] == 5
    assert video_job["image_count"] == 0
    assert video_job["video_price_fen"] == "25"
    assert video_job["cost_fen"] == "125"
    assert first_image_job["operation"] == "image.generate"
    assert first_image_job["image_count"] == 1
    assert first_image_job["video_seconds"] == 0
    assert first_image_job["image_price_fen"] == "120"
    assert first_image_job["cost_fen"] == "120"
    assert later_image_job["operation"] == "image.generate"
    assert later_image_job["image_count"] == 1
    assert later_image_job["video_seconds"] == 0
    assert later_image_job["image_price_fen"] == "999"
    assert later_image_job["cost_fen"] == "999"
    assert owner_usage.json()["summary"]["total_cost_fen"] == "1244"
    assert user.get("/api/v1/admin/usage").status_code == 404

    admin_usage = admin.get("/api/v1/admin/usage")
    assert admin_usage.status_code == 200
    assert admin_usage.json()["summary"]["total_cost_fen"] == "1244"
    user_summary = next(item["summary"] for item in admin_usage.json()["users"] if item["user_id"] == accounts.user.user_id)
    assert user_summary["total_cost_fen"] == "1244"

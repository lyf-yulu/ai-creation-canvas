from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import JobRequest, JobState, ModelSpec, RequestContext, UpstreamJob
from ai_creation_canvas.domain.registry import AdapterRegistry


ORIGIN = "http://127.0.0.1:8992"


class AssignmentAdapter:
    service_id = "assignment-fixture"

    async def list_models(self, context: RequestContext) -> tuple[ModelSpec, ...]:
        del context
        return (
            ModelSpec("visible-model", self.service_id, "可见模型", ("image.generate",), ("text",), {}),
            ModelSpec("hidden-model", self.service_id, "隐藏模型", ("image.generate",), ("text",), {}),
            ModelSpec(
                "duration-model",
                self.service_id,
                "时长模型",
                ("video.generate",),
                ("text",),
                {
                    "type": "object",
                    "properties": {"duration": {"type": "integer", "minimum": 5, "maximum": 5}},
                    "additionalProperties": False,
                },
            ),
            ModelSpec(
                "wide-duration-model",
                self.service_id,
                "宽时长模型",
                ("video.generate",),
                ("text",),
                {
                    "type": "object",
                    "properties": {"duration": {"type": "integer", "minimum": 1, "maximum": 90_000}},
                    "additionalProperties": False,
                },
            ),
        )

    async def submit(self, context: RequestContext, request: JobRequest) -> UpstreamJob:
        del context, request
        return UpstreamJob(self.service_id, "upstream-1", JobState("upstream-1", "queued"), datetime.now(UTC))

    async def poll(self, context: RequestContext, upstream_job_id: str) -> JobState:
        del context
        return JobState(upstream_job_id, "queued")


def local_clients(tmp_path, *, user_model_ids: tuple[str, ...] = ("visible-model",)):
    registry = AdapterRegistry()
    registry.register_generation(AssignmentAdapter())
    settings = Settings(
        "test",
        8992,
        tmp_path / "data",
        "test-secret",
        identity_mode="local",
        allowed_origins=(ORIGIN,),
    )
    app = create_app(settings, static_dir=tmp_path / "dist", registry=registry, model_catalog=ModelCatalog(registry))
    accounts = app.state.local_auth.bootstrap_accounts(user_model_ids)
    admin = TestClient(app, base_url=ORIGIN)
    user = TestClient(app, base_url=ORIGIN)
    admin_login = admin.post("/api/v1/auth/login", json={"username": accounts.admin_username, "password": accounts.admin_password}).json()
    user_login = user.post("/api/v1/auth/login", json={"username": accounts.user_username, "password": accounts.user_password}).json()
    admin_changed = admin.post(
        "/api/v1/auth/change-password",
        headers={"Origin": ORIGIN, "X-CSRF-Token": admin_login["csrf_token"]},
        json={"current_password": accounts.admin_password, "new_password": "new-admin-correct-horse"},
    ).json()
    user_changed = user.post(
        "/api/v1/auth/change-password",
        headers={"Origin": ORIGIN, "X-CSRF-Token": user_login["csrf_token"]},
        json={"current_password": accounts.user_password, "new_password": "new-user-correct-horse"},
    ).json()
    admin_headers = {"Origin": ORIGIN, "X-CSRF-Token": admin_changed["csrf_token"]}
    user_headers = {"Origin": ORIGIN, "X-CSRF-Token": user_changed["csrf_token"]}
    return app, accounts, admin, user, admin_headers, user_headers


def job_payload(
    model_id: str,
    *,
    operation: str = "image.generate",
    params: dict[str, object] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, object]:
    return {
        "operation": operation,
        "model_id": model_id,
        "prompt": "test prompt",
        "params": params or {},
        "asset_ids": [],
        "idempotency_key": idempotency_key or f"key-{model_id}",
    }


def test_unassigned_model_is_hidden_and_cannot_be_submitted(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, admin, admin_headers

    models = user.get("/api/v1/models").json()["models"]
    hidden = user.post("/api/v1/jobs", headers=user_headers, json=job_payload("hidden-model"))

    assert [item["model_id"] for item in models] == ["visible-model"]
    assert hidden.status_code == 400
    assert hidden.json()["code"] == "MODEL_UNAVAILABLE"


def test_admin_can_atomically_replace_user_model_assignments(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, user_headers
    assert accounts.user is not None

    response = admin.put(
        f"/api/v1/admin/users/{accounts.user.user_id}/models",
        headers=admin_headers,
        json={"model_ids": ["hidden-model"]},
    )

    assert response.status_code == 200
    assert response.json()["model_ids"] == ["hidden-model"]
    assert [item["model_id"] for item in user.get("/api/v1/models").json()["models"]] == ["hidden-model"]


def test_normal_user_cannot_call_admin_api(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, admin, admin_headers

    assert user.get("/api/v1/admin/users").status_code == 404
    assert user.patch("/api/v1/admin/users/anything", headers=user_headers, json={"enabled": False}).status_code == 404


def test_video_duration_uses_only_declared_integer_billing_quantity(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(
        tmp_path, user_model_ids=("visible-model", "duration-model")
    )
    del admin, admin_headers
    assert accounts.user is not None

    charged = user.post(
        "/api/v1/jobs",
        headers=user_headers,
        json=job_payload(
            "duration-model",
            operation="video.generate",
            params={"duration": 5},
            idempotency_key="duration-present",
        ),
    )
    unmetered = user.post(
        "/api/v1/jobs",
        headers=user_headers,
        json=job_payload(
            "duration-model",
            operation="video.generate",
            idempotency_key="duration-absent",
        ),
    )

    assert charged.status_code == unmetered.status_code == 201
    charged_job, charged_forbidden = app.state.canvas_store.job_for_owner(charged.json()["id"], accounts.user.user_id)
    unmetered_job, unmetered_forbidden = app.state.canvas_store.job_for_owner(unmetered.json()["id"], accounts.user.user_id)
    assert charged_forbidden is unmetered_forbidden is False
    assert charged_job is not None and charged_job["video_seconds"] == 5
    assert unmetered_job is not None and unmetered_job["video_seconds"] == 0


def test_catalog_duration_beyond_store_limit_creates_an_unmetered_job(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(
        tmp_path, user_model_ids=("wide-duration-model",)
    )
    del admin, admin_headers
    assert accounts.user is not None

    response = user.post(
        "/api/v1/jobs",
        headers=user_headers,
        json=job_payload(
            "wide-duration-model",
            operation="video.generate",
            params={"duration": 90_000},
            idempotency_key="duration-beyond-store-limit",
        ),
    )
    maximum = user.post(
        "/api/v1/jobs",
        headers=user_headers,
        json=job_payload(
            "wide-duration-model",
            operation="video.generate",
            params={"duration": 86_400},
            idempotency_key="duration-at-store-limit",
        ),
    )

    assert response.status_code == maximum.status_code == 201
    job, forbidden = app.state.canvas_store.job_for_owner(
        response.json()["id"], accounts.user.user_id
    )
    assert forbidden is False
    assert job is not None and job["video_seconds"] == 0
    maximum_job, maximum_forbidden = app.state.canvas_store.job_for_owner(
        maximum.json()["id"], accounts.user.user_id
    )
    assert maximum_forbidden is False
    assert maximum_job is not None and maximum_job["video_seconds"] == 86_400

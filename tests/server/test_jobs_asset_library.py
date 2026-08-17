from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import (
    JobRequest,
    JobState,
    ModelInputPort,
    ModelOperation,
    ModelSpec,
    RequestContext,
    UpstreamJob,
)
from ai_creation_canvas.domain.registry import AdapterRegistry
from tests.contracts.test_generation_flow import headers


class RecordingVideoAdapter:
    service_id = "ark-video"
    supports_background_polling = True

    def __init__(self) -> None:
        self.submitted: list[JobRequest] = []

    async def list_models(self, context: RequestContext) -> tuple[ModelSpec, ...]:
        del context
        return (
            ModelSpec(
                "seedance-v1", self.service_id, "Seedance", ("video.generate",), ("text", "image"),
                {"type": "object", "properties": {}, "additionalProperties": False},
                None,
                (
                    ModelInputPort("prompt", "text", 1, 1),
                    ModelInputPort("reference_images", "image", 0, 30, asset_kind="library"),
                    ModelInputPort("first_frame", "image", 0, 1),
                ),
                {},
            ),
        )

    async def submit(self, context: RequestContext, request: JobRequest) -> UpstreamJob:
        del context
        self.submitted.append(request)
        return UpstreamJob(self.service_id, "upstream-1", JobState("upstream-1", "queued"), datetime.now(UTC))

    async def poll(self, context: RequestContext, upstream_job_id: str) -> JobState:
        del context
        return JobState(upstream_job_id, "queued")


def client(tmp_path: Path) -> tuple[TestClient, RecordingVideoAdapter]:
    adapter = RecordingVideoAdapter()
    registry = AdapterRegistry()
    registry.register_generation(adapter)
    settings = Settings("test", 8992, tmp_path / "data", "test-secret")
    app = create_app(settings, registry=registry, model_catalog=ModelCatalog(registry))
    return TestClient(app, raise_server_exceptions=False), adapter


def add_library_asset(tmp_path: Path, *, asset_id: str, user_id: str = "u-a", status: str = "active", service_id: str = "ark-video", upstream_asset_id: str = "asset-abc123") -> None:
    from ai_creation_canvas.storage.sqlite import CanvasStore

    store = CanvasStore(tmp_path / "data")
    store.create_asset(
        asset_id=asset_id, user_id=user_id, kind="library", media_type="image", mime_type="image/png",
        relative_path=f"assets/{asset_id}.png", size_bytes=16, status=status,
        service_id=service_id, upstream_asset_id=upstream_asset_id,
    )


def submit(client: TestClient, *, inputs: dict[str, list[str]] | None = None, asset_ids: list[str] | None = None, user: str = "u-a", key: str = "key-1"):
    return client.post(
        "/api/v1/jobs",
        json={
            "operation": "video.generate",
            "model_id": "seedance-v1",
            "prompt": "animate the portrait",
            "params": {},
            "asset_ids": asset_ids or [],
            "inputs": inputs or {},
            "idempotency_key": key,
        },
        headers=headers(user=user),
    )


def test_library_asset_binds_to_declared_library_port(tmp_path: Path) -> None:
    client_, adapter = client(tmp_path)
    add_library_asset(tmp_path, asset_id="lib-1")

    response = submit(client_, inputs={"reference_images": ["lib-1"]})

    assert response.status_code == 201
    assert adapter.submitted[0].inputs == {"reference_images": ("asset://asset-abc123",)}
    assert "asset://asset-abc123" not in adapter.submitted[0].asset_ids


def test_library_asset_rejected_on_undeclared_port_and_legacy_ids(tmp_path: Path) -> None:
    client_, adapter = client(tmp_path)
    add_library_asset(tmp_path, asset_id="lib-1")

    first_frame = submit(client_, inputs={"first_frame": ["lib-1"]}, key="key-2")
    assert first_frame.status_code == 400 and first_frame.json()["code"] == "ASSET_INVALID"

    legacy = submit(client_, asset_ids=["lib-1"], key="key-3")
    assert legacy.status_code == 400 and legacy.json()["code"] == "ASSET_INVALID"

    assert adapter.submitted == []


def test_library_asset_requires_matching_service_and_active_status(tmp_path: Path) -> None:
    client_, adapter = client(tmp_path)
    add_library_asset(tmp_path, asset_id="lib-mismatch", service_id="ark-image")
    add_library_asset(tmp_path, asset_id="lib-bad-upstream", upstream_asset_id="not-an-ark-id")
    add_library_asset(tmp_path, asset_id="lib-processing", status="processing")

    for asset_id in ("lib-mismatch", "lib-bad-upstream", "lib-processing"):
        response = submit(client_, inputs={"reference_images": [asset_id]}, key=f"key-{asset_id}")
        assert response.status_code == 400 and response.json()["code"] == "ASSET_INVALID"

    assert adapter.submitted == []


def test_forged_asset_uri_strings_never_reach_the_adapter(tmp_path: Path) -> None:
    client_, adapter = client(tmp_path)

    response = submit(client_, inputs={"reference_images": ["asset://asset-abc123"]})

    assert response.status_code == 400 and response.json()["code"] == "ASSET_INVALID"
    assert adapter.submitted == []


def test_other_users_library_assets_are_forbidden(tmp_path: Path) -> None:
    client_, adapter = client(tmp_path)
    add_library_asset(tmp_path, asset_id="lib-a", user_id="u-a")

    response = submit(client_, inputs={"reference_images": ["lib-a"]}, user="u-b")

    assert response.status_code == 403
    assert adapter.submitted == []

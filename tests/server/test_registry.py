from dataclasses import dataclass

import pytest

from ai_creation_canvas.domain.models import (
    AssetRef,
    AssetStatus,
    JobRequest,
    ModelOperation,
    ModelSpec,
    PortalUser,
    RequestContext,
)
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.errors import AdapterNotFoundError, AdapterRegistrationError


@dataclass
class FakeGenerationAdapter:
    service_id: str

    async def list_models(self, context: RequestContext) -> tuple[ModelSpec, ...]:
        return ()

    async def submit(self, context: RequestContext, request: JobRequest):
        raise NotImplementedError

    async def poll(self, context: RequestContext, upstream_job_id: str):
        raise NotImplementedError


@dataclass
class FakeAssetAdapter:
    service_id: str

    async def upload(self, context: RequestContext, asset: AssetRef) -> AssetRef:
        return asset

    async def get(self, context: RequestContext, asset_id: str) -> AssetRef:
        raise NotImplementedError


@dataclass
class FakeUsageAdapter:
    service_id: str

    async def record(self, context: RequestContext, upstream_job_id: str) -> None:
        return None


def test_registers_generation_adapter_without_core_branch():
    registry = AdapterRegistry()
    fake = FakeGenerationAdapter(service_id="fake-image")

    registry.register_generation(fake)

    assert registry.generation("fake-image") is fake
    with pytest.raises(AdapterRegistrationError, match="duplicate service_id"):
        registry.register_generation(fake)


def test_allows_same_service_id_once_per_port_category():
    registry = AdapterRegistry()
    service_id = "portal-backed-service"
    generation = FakeGenerationAdapter(service_id)
    asset = FakeAssetAdapter(service_id)
    usage = FakeUsageAdapter(service_id)

    registry.register_generation(generation)
    registry.register_asset(asset)
    registry.register_usage(usage)

    assert registry.generation(service_id) is generation
    assert registry.asset(service_id) is asset
    assert registry.usage(service_id) is usage


def test_rejects_adapter_without_a_stable_service_id():
    registry = AdapterRegistry()

    with pytest.raises(AdapterRegistrationError, match="service_id"):
        registry.register_generation(FakeGenerationAdapter(" "))


def test_rejects_adapter_missing_required_port_method():
    registry = AdapterRegistry()

    @dataclass
    class IncompleteGenerationAdapter:
        service_id: str = "incomplete"

        async def list_models(self, context: RequestContext) -> tuple[ModelSpec, ...]:
            return ()

        async def poll(self, context: RequestContext, upstream_job_id: str):
            raise NotImplementedError

    with pytest.raises(AdapterRegistrationError, match="submit"):
        registry.register_generation(IncompleteGenerationAdapter())


def test_unknown_service_becomes_structured_domain_error():
    registry = AdapterRegistry()

    with pytest.raises(AdapterNotFoundError) as raised:
        registry.generation("not-registered")

    error = raised.value.api_error
    assert error.code == "SERVICE_NOT_FOUND"
    assert error.retryable is False
    assert error.phase == "adapter_lookup"
    assert "not-registered" in error.message


def test_domain_values_reject_invalid_stable_ids_operations_and_states():
    user = PortalUser(user_id="user-1", username="Ada", role="user")
    context = RequestContext(user=user, request_id="request-1", trace_id="trace-1")

    with pytest.raises(ValueError, match="model_id"):
        ModelSpec(
            model_id="",
            service_id="image-service",
            display_name="Image",
            operations=(ModelOperation.IMAGE_GENERATE,),
        )
    with pytest.raises(ValueError, match="operation"):
        JobRequest(
            operation="not-an-operation",  # type: ignore[arg-type]
            model_id="model-1",
            prompt="hello",
            idempotency_key="idem-1",
        )
    with pytest.raises(ValueError, match="status"):
        AssetRef(
            asset_id="asset-1",
            kind="reference",
            status="unknown",  # type: ignore[arg-type]
            mime_type="image/png",
        )

    assert context.user is user


def test_domain_collections_are_snapshotted_from_mutable_inputs():
    media = ["text", "image"]
    assets = ["asset-1"]
    params = {"width": 1024}
    model = ModelSpec(
        model_id="model-1",
        service_id="image-service",
        display_name="Image",
        operations=["image.generate"],
        input_media=media,
        parameter_schema=params,
    )
    request = JobRequest(
        operation="image.generate",
        model_id="model-1",
        prompt="hello",
        idempotency_key="idem-1",
        asset_ids=assets,
    )

    media.append("video")
    assets.append("asset-2")
    params["height"] = 1024

    assert model.input_media == ("text", "image")
    assert request.asset_ids == ("asset-1",)
    assert dict(model.parameter_schema) == {"width": 1024}

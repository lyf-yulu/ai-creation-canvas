from dataclasses import asdict, dataclass
import math

import pytest
from fastapi.encoders import jsonable_encoder

from ai_creation_canvas.comfy.service import ComfyServiceHealth, ComfyServiceStatus, ComfyWorkflowRequest
from ai_creation_canvas.domain.models import (
    AssetRef,
    AssetStatus,
    JobRequest,
    JobState,
    JobStatus,
    ModelOperation,
    ModelSpec,
    PortalUser,
    RequestContext,
    UpstreamJob,
)
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.errors import ApiError, AdapterNotFoundError, AdapterRegistrationError


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


def test_domain_parameter_values_are_deeply_immutable_and_json_encodable():
    schema = {"limits": {"sizes": [512, 1024]}}
    params = {"style": {"palette": ["warm"]}}
    model = ModelSpec(
        model_id="model-1",
        service_id="image-service",
        display_name="Image",
        operations=["image.generate"],
        parameter_schema=schema,
    )
    request = JobRequest(
        operation="image.generate",
        model_id="model-1",
        prompt="hello",
        idempotency_key="idem-1",
        params=params,
    )
    schema["limits"]["sizes"].append(2048)
    params["style"]["palette"].append("cool")

    with pytest.raises((AttributeError, TypeError)):
        model.parameter_schema["limits"]["sizes"].append(4096)  # type: ignore[attr-defined]
    assert jsonable_encoder(model) == {
        "model_id": "model-1",
        "service_id": "image-service",
        "display_name": "Image",
        "operations": ["image.generate"],
        "input_media": [],
        "parameter_schema": {"limits": {"sizes": [512, 1024]}},
        "requires_asset_kind": None,
        "input_ports": [],
        "parameter_mappings": {},
    }
    assert jsonable_encoder(request)["params"] == {"style": {"palette": ["warm"]}}
    assert asdict(model)["parameter_schema"] == {"limits": {"sizes": [512, 1024]}}


@pytest.mark.parametrize("non_finite", (math.nan, math.inf, -math.inf))
@pytest.mark.parametrize(
    ("domain_factory", "payload"),
    [
        (
            lambda payload: ModelSpec("model-1", "image-service", "Image", ("image.generate",), parameter_schema=payload),
            lambda value: {"value": value},
        ),
        (
            lambda payload: ModelSpec("model-1", "image-service", "Image", ("image.generate",), parameter_schema=payload),
            lambda value: {"values": [value]},
        ),
        (
            lambda payload: ModelSpec("model-1", "image-service", "Image", ("image.generate",), parameter_schema=payload),
            lambda value: {"nested": {"value": value}},
        ),
        (
            lambda payload: JobRequest("image.generate", "model-1", "hello", "idem-1", params=payload),
            lambda value: {"value": value},
        ),
        (
            lambda payload: JobRequest("image.generate", "model-1", "hello", "idem-1", params=payload),
            lambda value: {"values": [value]},
        ),
        (
            lambda payload: JobRequest("image.generate", "model-1", "hello", "idem-1", params=payload),
            lambda value: {"nested": {"value": value}},
        ),
    ],
)
def test_domain_parameter_values_reject_non_finite_floats_without_echoing_them(domain_factory, payload, non_finite):
    with pytest.raises(ValueError, match="finite") as raised:
        domain_factory(payload(non_finite))

    assert str(non_finite) not in str(raised.value)


def test_domain_parameter_values_preserve_finite_json_scalars():
    request = JobRequest(
        "image.generate",
        "model-1",
        "hello",
        "idem-1",
        params={"integer": 1, "boolean": True, "empty": None, "finite": 1.5},
    )

    assert asdict(request)["params"] == {"integer": 1, "boolean": True, "empty": None, "finite": 1.5}


def _result() -> AssetRef:
    return AssetRef("result-1", "reference", "active", "image/png")


def _error() -> ApiError:
    return ApiError("TASK_FAILED", "Task failed.", False, "request-1", "polling")


@pytest.mark.parametrize(
    ("status", "result", "error", "match"),
    [
        (JobStatus.SUCCEEDED, None, None, "succeeded jobs require a result"),
        (JobStatus.SUCCEEDED, _result(), _error(), "succeeded jobs cannot include an error"),
        (JobStatus.FAILED, None, None, "failed jobs require an error"),
        (JobStatus.FAILED, _result(), _error(), "failed jobs cannot include a result"),
        (JobStatus.UPLOADING, _result(), None, "in-progress jobs cannot include a result or error"),
        (JobStatus.SUBMITTING, None, _error(), "in-progress jobs cannot include a result or error"),
        (JobStatus.QUEUED, _result(), None, "in-progress jobs cannot include a result or error"),
        (JobStatus.RUNNING, None, _error(), "in-progress jobs cannot include a result or error"),
    ],
)
def test_job_state_rejects_illegal_status_field_combinations(status, result, error, match):
    with pytest.raises(ValueError, match=match):
        JobState("job-1", status, result=result, error=error)


def test_job_state_rejects_non_api_error_and_empty_string_result():
    with pytest.raises(ValueError, match="error must be an ApiError"):
        JobState("job-1", "failed", error="not-an-error")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="result must be an AssetRef"):
        JobState("job-1", "succeeded", result="")  # type: ignore[arg-type]


def test_upstream_job_carries_only_a_validated_job_state():
    state = JobState("job-1", "succeeded", result=_result())
    job = UpstreamJob("image-service", "upstream-1", state)

    assert job.state is state


async def _wrong_asset_get(self):
    return None


async def _wrong_usage_record(self, context):
    return None


@pytest.mark.parametrize(
    ("register", "adapter", "method"),
    [
        (
            lambda registry, adapter: registry.register_generation(adapter),
            type(
                "SyncGeneration",
                (),
                {
                    "service_id": "sync-generation",
                    "list_models": lambda self, context: (),
                    "submit": FakeGenerationAdapter.submit,
                    "poll": FakeGenerationAdapter.poll,
                },
            )(),
            "list_models",
        ),
        (
            lambda registry, adapter: registry.register_asset(adapter),
            type(
                "WrongAssetSignature",
                (),
                {
                    "service_id": "wrong-asset",
                    "upload": FakeAssetAdapter.upload,
                    "get": _wrong_asset_get,
                },
            )(),
            "get",
        ),
        (
            lambda registry, adapter: registry.register_usage(adapter),
            type(
                "WrongUsageSignature",
                (),
                {"service_id": "wrong-usage", "record": _wrong_usage_record},
            )(),
            "record",
        ),
    ],
)
def test_registry_rejects_sync_or_wrong_signature_port_methods(register, adapter, method):
    with pytest.raises(AdapterRegistrationError, match=method):
        register(AdapterRegistry(), adapter)


def test_registry_hides_exception_raised_by_service_id_property():
    class RaisingServiceId:
        @property
        def service_id(self):
            raise RuntimeError("secret service-id getter failure")

    with pytest.raises(AdapterRegistrationError) as raised:
        AdapterRegistry().register_generation(RaisingServiceId())

    assert "secret" not in str(raised.value)
    assert "secret" not in repr(raised.value)


def test_registry_hides_exception_raised_by_required_method_property():
    class RaisingMethod:
        service_id = "raising-method"

        async def list_models(self, context):
            return ()

        @property
        def submit(self):
            raise RuntimeError("secret submit getter failure")

        async def poll(self, context, upstream_job_id):
            raise NotImplementedError

    with pytest.raises(AdapterRegistrationError) as raised:
        AdapterRegistry().register_generation(RaisingMethod())

    assert "secret" not in str(raised.value)
    assert "secret" not in repr(raised.value)


def test_registry_does_not_render_a_non_callable_method_value():
    class SecretRepresentation:
        def __repr__(self):
            return "secret non-callable value"

    class NonCallableMethod:
        service_id = "non-callable"

        async def list_models(self, context):
            return ()

        submit = SecretRepresentation()

        async def poll(self, context, upstream_job_id):
            raise NotImplementedError

    with pytest.raises(AdapterRegistrationError) as raised:
        AdapterRegistry().register_generation(NonCallableMethod())

    assert "secret" not in str(raised.value)
    assert "secret" not in repr(raised.value)


async def _only_varargs(self, *args):
    return None


@pytest.mark.parametrize(
    ("register", "adapter", "method"),
    [
        (
            lambda registry, adapter: registry.register_generation(adapter),
            type(
                "VarArgsGeneration",
                (),
                {
                    "service_id": "varargs-generation",
                    "list_models": _only_varargs,
                    "submit": FakeGenerationAdapter.submit,
                    "poll": FakeGenerationAdapter.poll,
                },
            )(),
            "list_models",
        ),
        (
            lambda registry, adapter: registry.register_asset(adapter),
            type(
                "VarArgsAsset",
                (),
                {"service_id": "varargs-asset", "upload": _only_varargs, "get": FakeAssetAdapter.get},
            )(),
            "upload",
        ),
        (
            lambda registry, adapter: registry.register_usage(adapter),
            type("VarArgsUsage", (), {"service_id": "varargs-usage", "record": _only_varargs})(),
            "record",
        ),
    ],
)
def test_registry_rejects_varargs_in_place_of_explicit_port_parameters(register, adapter, method):
    with pytest.raises(AdapterRegistrationError, match=method):
        register(AdapterRegistry(), adapter)


def test_registry_rejects_signature_inspection_errors_without_leaking_details():
    class SignatureRaises:
        async def __call__(self, context):
            return ()

        @property
        def __signature__(self):
            raise RuntimeError("secret signature failure")

    class Adapter:
        service_id = "signature-raises"
        list_models = SignatureRaises()
        submit = FakeGenerationAdapter.submit
        poll = FakeGenerationAdapter.poll

    with pytest.raises(AdapterRegistrationError) as raised:
        AdapterRegistry().register_generation(Adapter())

    assert "secret" not in str(raised.value)
    assert "secret" not in repr(raised.value)


def test_registry_rejects_extra_required_positional_arguments():
    class Adapter:
        service_id = "extra-required"

        async def list_models(self, context, required):
            return ()

        submit = FakeGenerationAdapter.submit
        poll = FakeGenerationAdapter.poll

    with pytest.raises(AdapterRegistrationError, match="list_models"):
        AdapterRegistry().register_generation(Adapter())


def test_registry_accepts_optional_keyword_only_extension_arguments():
    class Adapter:
        service_id = "optional-keyword-only"

        async def list_models(self, context, *, page_size=50):
            return ()

        submit = FakeGenerationAdapter.submit
        poll = FakeGenerationAdapter.poll

    registry = AdapterRegistry()
    adapter = Adapter()
    registry.register_generation(adapter)

    assert registry.generation(adapter.service_id) is adapter


@dataclass
class FakeComfyWorkflowAdapter:
    service_id: str

    async def health(self, context: RequestContext) -> ComfyServiceHealth:
        return ComfyServiceHealth(self.service_id, ComfyServiceStatus.HEALTHY, frozenset())

    async def list_node_types(self, context: RequestContext) -> frozenset[str]:
        return frozenset()

    async def submit(self, context: RequestContext, request: ComfyWorkflowRequest) -> UpstreamJob:
        raise NotImplementedError

    async def poll(self, context: RequestContext, upstream_job_id: str) -> JobState:
        raise NotImplementedError

    async def cancel(self, context: RequestContext, upstream_job_id: str) -> None:
        return None


def test_registry_keeps_comfy_workflow_adapters_outside_generic_generation_port() -> None:
    registry = AdapterRegistry()
    adapter = FakeComfyWorkflowAdapter("comfy-local")

    registry.register_comfy_workflow(adapter)

    assert registry.comfy_workflow("comfy-local") is adapter
    assert registry.comfy_workflow_adapters() == (adapter,)
    with pytest.raises(AdapterNotFoundError):
        registry.generation("comfy-local")


def test_registry_rejects_comfy_adapter_with_wrong_async_signature() -> None:
    class WrongComfyAdapter(FakeComfyWorkflowAdapter):
        async def cancel(self, context: RequestContext) -> None:  # type: ignore[override]
            return None

    with pytest.raises(AdapterRegistrationError, match="cancel"):
        AdapterRegistry().register_comfy_workflow(WrongComfyAdapter("comfy-local"))

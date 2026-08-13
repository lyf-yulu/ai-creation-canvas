from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ai_creation_canvas.adapters.chiyun import ChiyunGenerationAdapter
from ai_creation_canvas.adapters.factory import AdapterFactory, MappingCredentialResolver
from ai_creation_canvas.domain.models import ModelInputPort, ModelOperation
from ai_creation_canvas.model_registry import GovernedModelDefinition, ModelModality, OperationContract, ProviderDefinition


def _provider() -> ProviderDefinition:
    return ProviderDefinition("chiyun", "Chiyun", "chiyun_openai_images", "https://chiyun.example", "chiyun-primary")


def _model(**changes: object) -> GovernedModelDefinition:
    values: dict[str, object] = {
        "model_id": "chiyun-gpt-image-2", "provider_id": "chiyun", "provider_model_name": "gpt-image-2",
        "display_name": "GPT Image 2", "introduction": "edit", "modality": ModelModality.IMAGE,
        "operation_contracts": (OperationContract(ModelOperation.IMAGE_EDIT, (ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 1, 10)), "image", {"type": "object", "properties": {}, "additionalProperties": False}, {}),),
    }
    values.update(changes)
    return GovernedModelDefinition(**values)  # type: ignore[arg-type]


def test_factory_constructs_only_allowlisted_adapters_and_keeps_credentials_server_side(tmp_path: Path) -> None:
    resolver = MappingCredentialResolver({"chiyun-primary": "test-only-secret"})
    factory = AdapterFactory(data_dir=tmp_path, credential_resolver=resolver, asset_loader=lambda _: (b"image", "image/png"), transport=httpx.MockTransport(lambda _: httpx.Response(500)), trusted_provider_origins={("chiyun", "chiyun_openai_images"): "https://chiyun.example"})

    adapter = factory.build(_provider(), (_model(),))
    assert isinstance(adapter, ChiyunGenerationAdapter)
    assert adapter.service_id == "chiyun"
    assert adapter.model_ids == ("chiyun-gpt-image-2",)
    assert "test-only-secret" not in repr(adapter)
    assert factory.build(_provider(), (_model(),)) is adapter


def test_factory_rejects_missing_credentials_and_provider_model_mismatch(tmp_path: Path) -> None:
    factory = AdapterFactory(data_dir=tmp_path, credential_resolver=MappingCredentialResolver({}), asset_loader=lambda _: (b"image", "image/png"), trusted_provider_origins={("chiyun", "chiyun_openai_images"): "https://chiyun.example"})
    with pytest.raises(ValueError):
        factory.build(_provider(), (_model(),))
    with pytest.raises(ValueError):
        AdapterFactory(data_dir=tmp_path, credential_resolver=MappingCredentialResolver({"chiyun-primary": "test-only-secret"}), asset_loader=lambda _: (b"image", "image/png"), trusted_provider_origins={("chiyun", "chiyun_openai_images"): "https://chiyun.example"}).build(_provider(), (_model(provider_id="other"),))

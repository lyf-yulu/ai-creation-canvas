"""Allowlisted construction of adapters from governed definitions."""
from __future__ import annotations

from pathlib import Path
import os
import re
from typing import Callable, Mapping, Protocol

import httpx

from ai_creation_canvas.adapters.chiyun import ChiyunGenerationAdapter
from ai_creation_canvas.model_registry import GovernedModelDefinition, ProviderDefinition


class CredentialResolver(Protocol):
    def resolve(self, reference: str) -> str: ...


class MappingCredentialResolver:
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def resolve(self, reference: str) -> str:
        value = self._values.get(reference)
        if not isinstance(value, str) or len(value.strip()) < 8:
            raise ValueError("credential reference is unavailable")
        return value.strip()


class EnvironmentCredentialResolver:
    """Resolve deployment-owned references without ever persisting the secret."""
    def resolve(self, reference: str) -> str:
        if not isinstance(reference, str) or not reference:
            raise ValueError("credential reference is unavailable")
        name = "AICC_CREDENTIAL_" + re.sub(r"[^A-Za-z0-9]", "_", reference).upper()
        value = os.environ.get(name)
        if not isinstance(value, str) or len(value.strip()) < 8:
            raise ValueError("credential reference is unavailable")
        return value.strip()


class AdapterFactory:
    def __init__(self, *, data_dir: Path | str, credential_resolver: CredentialResolver, asset_loader: Callable[[str], tuple[bytes, str]], transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._data_dir, self._credential_resolver, self._asset_loader, self._transport = Path(data_dir), credential_resolver, asset_loader, transport
        self._cache: dict[tuple[str, int, tuple[tuple[str, int], ...]], object] = {}

    def build(self, provider: ProviderDefinition, models: tuple[GovernedModelDefinition, ...]):
        if not models or any(model.provider_id != provider.provider_id for model in models):
            raise ValueError("provider model binding is invalid")
        key = (provider.provider_id, provider.revision, tuple(sorted((model.model_id, model.revision) for model in models)))
        if key in self._cache:
            return self._cache[key]
        if provider.adapter_type != "chiyun_openai_images":
            raise ValueError("adapter type is not supported by the governed factory")
        adapter = ChiyunGenerationAdapter(
            provider=provider, models=models, api_key=self._credential_resolver.resolve(provider.credential_ref),
            data_dir=self._data_dir, asset_loader=self._asset_loader, transport=self._transport,
        )
        self._cache[key] = adapter
        return adapter

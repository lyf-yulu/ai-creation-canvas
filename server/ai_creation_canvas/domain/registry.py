"""Registry for trusted, provider-neutral adapter capabilities."""

from typing import TypeVar

from ai_creation_canvas.domain.ports import AssetPort, GenerationPort, UsagePort
from ai_creation_canvas.errors import AdapterNotFoundError, AdapterRegistrationError, ApiError


PortT = TypeVar("PortT")


class AdapterRegistry:
    """One adapter per service and capability; categories may share a service ID."""

    def __init__(self) -> None:
        self._generation: dict[str, GenerationPort] = {}
        self._assets: dict[str, AssetPort] = {}
        self._usage: dict[str, UsagePort] = {}

    def register_generation(self, adapter: GenerationPort) -> None:
        self._register(self._generation, adapter, ("list_models", "submit", "poll"), "generation")

    def register_asset(self, adapter: AssetPort) -> None:
        self._register(self._assets, adapter, ("upload", "get"), "asset")

    def register_usage(self, adapter: UsagePort) -> None:
        self._register(self._usage, adapter, ("record",), "usage")

    def generation(self, service_id: str) -> GenerationPort:
        return self._get(self._generation, service_id, "generation")

    def asset(self, service_id: str) -> AssetPort:
        return self._get(self._assets, service_id, "asset")

    def usage(self, service_id: str) -> UsagePort:
        return self._get(self._usage, service_id, "usage")

    @staticmethod
    def _register(
        adapters: dict[str, PortT], adapter: PortT, methods: tuple[str, ...], category: str
    ) -> None:
        service_id = getattr(adapter, "service_id", None)
        if not isinstance(service_id, str) or not service_id.strip():
            raise AdapterRegistrationError(f"{category} adapter service_id must be a non-empty stable identifier")
        for method in methods:
            if not callable(getattr(adapter, method, None)):
                raise AdapterRegistrationError(f"{category} adapter {service_id!r} requires callable {method}")
        if service_id in adapters:
            raise AdapterRegistrationError(f"duplicate service_id for {category}: {service_id}")
        adapters[service_id] = adapter

    @staticmethod
    def _get(adapters: dict[str, PortT], service_id: str, category: str) -> PortT:
        adapter = adapters.get(service_id)
        if adapter is not None:
            return adapter
        raise AdapterNotFoundError(
            ApiError(
                code="SERVICE_NOT_FOUND",
                message=f"No {category} adapter is registered for service {service_id!r}.",
                retryable=False,
                request_id="adapter-registry",
                phase="adapter_lookup",
            )
        )

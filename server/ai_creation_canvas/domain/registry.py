"""Registry for trusted, provider-neutral adapter capabilities."""

from inspect import Parameter, iscoroutinefunction, signature
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
        self._register(
            self._generation,
            adapter,
            (("list_models", 1), ("submit", 2), ("poll", 2)),
            "generation",
        )

    def replace_generation(self, adapter: GenerationPort) -> None:
        """Replace one trusted service binding after validating the same port contract."""
        service_id = self._read_attribute(adapter, "service_id", "generation")
        if isinstance(service_id, str):
            self._generation.pop(service_id, None)
        self.register_generation(adapter)

    def unregister_generation(self, service_id: str) -> None:
        self._generation.pop(service_id, None)

    def register_asset(self, adapter: AssetPort) -> None:
        self._register(self._assets, adapter, (("upload", 2), ("get", 2)), "asset")

    def register_usage(self, adapter: UsagePort) -> None:
        self._register(self._usage, adapter, (("record", 2),), "usage")

    def generation(self, service_id: str) -> GenerationPort:
        return self._get(self._generation, service_id, "generation")

    def generation_adapters(self) -> tuple[GenerationPort, ...]:
        """Return trusted generation adapters in a stable order."""
        return tuple(self._generation[service_id] for service_id in sorted(self._generation))

    def asset(self, service_id: str) -> AssetPort:
        return self._get(self._assets, service_id, "asset")

    def usage(self, service_id: str) -> UsagePort:
        return self._get(self._usage, service_id, "usage")

    @staticmethod
    def _register(
        adapters: dict[str, PortT],
        adapter: PortT,
        methods: tuple[tuple[str, int], ...],
        category: str,
    ) -> None:
        service_id = AdapterRegistry._read_attribute(adapter, "service_id", category)
        if not isinstance(service_id, str) or not service_id.strip():
            raise AdapterRegistrationError(f"{category} adapter service_id must be a non-empty stable identifier")
        for method_name, argument_count in methods:
            method = AdapterRegistry._read_attribute(adapter, method_name, category)
            if not callable(method):
                raise AdapterRegistrationError(f"{category} adapter {service_id!r} requires callable {method_name}")
            if not AdapterRegistry._is_async_callable(method):
                raise AdapterRegistrationError(f"{category} adapter {service_id!r} requires async callable {method_name}")
            try:
                method_signature = signature(method)
                explicit_positional = tuple(
                    parameter
                    for parameter in method_signature.parameters.values()
                    if parameter.kind in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD)
                )
                if len(explicit_positional) < argument_count:
                    raise TypeError("insufficient explicit positional parameters")
                method_signature.bind(*([object()] * argument_count))
            except (TypeError, ValueError):
                raise AdapterRegistrationError(
                    f"{category} adapter {service_id!r} has incompatible signature for {method_name}"
                ) from None
            except Exception:
                raise AdapterRegistrationError(
                    f"{category} adapter {service_id!r} has inaccessible signature for {method_name}"
                ) from None
        if service_id in adapters:
            raise AdapterRegistrationError(f"duplicate service_id for {category}: {service_id}")
        adapters[service_id] = adapter

    @staticmethod
    def _read_attribute(adapter: object, attribute: str, category: str) -> object:
        try:
            return getattr(adapter, attribute)
        except Exception:
            raise AdapterRegistrationError(f"{category} adapter has inaccessible {attribute}") from None

    @staticmethod
    def _is_async_callable(value: object) -> bool:
        try:
            return iscoroutinefunction(value) or iscoroutinefunction(getattr(value, "__call__", None))
        except Exception:
            return False

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

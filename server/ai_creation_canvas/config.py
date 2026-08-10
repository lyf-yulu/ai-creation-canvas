"""Configuration that keeps test and production state strictly separate."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from ai_creation_canvas.adapters.portal.catalog import ServiceDeclaration
from ai_creation_canvas.domain.models import ModelOperation


_PRODUCTION_PORTS = frozenset({9090, 8787, 8797, 8891, 8991})
_PRODUCTION_REPOSITORY = Path("/Users/260413a/ai-generation-portable-apps")
_REJECTED_TOKENS = frozenset({"default", "changeme", "change-me", "test"})
_MAX_SERVICES_BYTES = 65536
_DANGEROUS_FIELDS = frozenset({"base_url", "url", "script", "plugin", "code", "api_key", "token", "headers"})


def load_service_declarations(path: Path | str, expected_root: Path | str) -> tuple[ServiceDeclaration, ...]:
    root = Path(expected_root).resolve(strict=False)
    candidate = Path(path)
    try:
        if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > _MAX_SERVICES_BYTES:
            raise ValueError("services configuration is not a safe regular file")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("services configuration is invalid") from error
    if not isinstance(payload, dict) or set(payload) != {"services"} or not isinstance(payload["services"], list):
        raise ValueError("services configuration is invalid")
    declarations = []
    for item in payload["services"]:
        if not isinstance(item, dict) or _DANGEROUS_FIELDS & set(item):
            raise ValueError("services configuration is invalid")
        service_id, mount, service_type, operations = (item.get("service_id"), item.get("mount"), item.get("service_type"), item.get("operations"))
        portrait = service_type == "portrait_asset"
        if set(item) != ({"service_id", "mount", "service_type", "operations", "contract_id", "routes"} if portrait else {"service_id", "mount", "service_type", "operations"}):
            raise ValueError("services configuration is invalid")
        if not isinstance(service_id, str) or not service_id.isascii() or not service_id.replace("-", "").replace("_", "").isalnum() or len(service_id) > 64:
            raise ValueError("services configuration is invalid")
        if service_type not in {"image", "video", "portrait_asset"} or not isinstance(operations, list):
            raise ValueError("services configuration is invalid")
        if not isinstance(mount, str) or not mount.startswith("/") or "%" in mount or any(part in {"", ".", ".."} for part in mount.split("/")[1:]) or any(ord(char) < 32 for char in mount):
            raise ValueError("services configuration is invalid")
        try:
            parsed = tuple(ModelOperation(value) for value in operations)
            contract_id = item.get("contract_id")
            routes = item.get("routes")
            if portrait:
                if contract_id != "portal-virtual-v1" or not isinstance(routes, dict) or set(routes) != {"catalog", "groups", "assets", "jobs"}:
                    raise ValueError
                for route in routes.values():
                    if not isinstance(route, str) or not route.startswith("api/") or route.startswith("//") or "%" in route or any(char in route for char in "?#\\") or any(ord(char) < 32 for char in route) or any(part in {"", ".", ".."} for part in route.split("/")):
                        raise ValueError
            declaration = ServiceDeclaration(service_id, mount, service_type, parsed, contract_id, routes)
        except (TypeError, ValueError) as error:
            raise ValueError("services configuration is invalid") from error
        declarations.append(declaration)
    if len({item.service_id for item in declarations}) != len(declarations):
        raise ValueError("services configuration is invalid")
    if len({item.mount for item in declarations}) != len(declarations):
        raise ValueError("services configuration is invalid")
    if any(len(item.operations) != len(set(item.operations)) for item in declarations):
        raise ValueError("services configuration is invalid")
    return tuple(declarations)


def _validate_token(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip().lower() in _REJECTED_TOKENS:
        raise ValueError("PORTAL_INTERNAL_TOKEN must be a non-default non-empty secret")
    return value


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    port: int
    data_dir: Path | str
    portal_internal_token: str
    signature_ttl_seconds: int = 60
    portal_base_url: str | None = None
    services_config_path: Path | str | None = None
    services_config_root: Path | str | None = None
    portal_allow_loopback_http: bool = False
    portal_ca_file: Path | str | None = None
    portal_max_concurrency: int = 8
    identity_mode: str = "signed_portal"
    session_ttl_seconds: int = 12 * 60 * 60
    session_cookie_name: str = "aicc_session"
    allowed_origins: tuple[str, ...] = ()
    enable_demo_adapter: bool = False
    enable_ark_adapter: bool = False
    ark_models_config_path: Path | str | None = None
    ark_models_config_root: Path | str | None = None

    def __post_init__(self) -> None:
        if self.environment not in {"test", "production", "development"}:
            raise ValueError("environment must be test, development, or production")
        if not isinstance(self.port, int) or isinstance(self.port, bool) or not 1 <= self.port <= 65535:
            raise ValueError("port must be a valid TCP port")
        data_dir = Path(self.data_dir).expanduser().resolve(strict=False)
        production_repo = _PRODUCTION_REPOSITORY.resolve(strict=False)
        if (self.environment == "test" or (self.identity_mode == "local" and self.environment != "production")) and self.port in _PRODUCTION_PORTS:
            raise ValueError("non-production environment cannot use a production port")
        if self.environment != "production" and _is_within(data_dir, production_repo):
            raise ValueError("non-production environment cannot use the production repository")
        if (
            not isinstance(self.signature_ttl_seconds, int)
            or isinstance(self.signature_ttl_seconds, bool)
            or self.signature_ttl_seconds < 1
        ):
            raise ValueError("signature_ttl_seconds must be positive")
        object.__setattr__(self, "data_dir", data_dir)
        object.__setattr__(self, "portal_internal_token", _validate_token(self.portal_internal_token))
        if type(self.portal_allow_loopback_http) is not bool:
            raise ValueError("portal_allow_loopback_http must be a bool")
        if type(self.portal_max_concurrency) is not int or self.portal_max_concurrency < 1:
            raise ValueError("portal_max_concurrency must be a positive integer")
        if self.identity_mode not in {"signed_portal", "local"}:
            raise ValueError("identity_mode must be signed_portal or local")
        if type(self.session_ttl_seconds) is not int or self.session_ttl_seconds < 1:
            raise ValueError("session_ttl_seconds must be positive")
        if not isinstance(self.session_cookie_name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", self.session_cookie_name):
            raise ValueError("session_cookie_name is invalid")
        if not isinstance(self.allowed_origins, tuple) or len(self.allowed_origins) > 16:
            raise ValueError("allowed_origins is invalid")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin) if isinstance(origin, str) else None
            if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                raise ValueError("allowed_origins is invalid")
        if self.identity_mode == "local" and not self.allowed_origins:
            raise ValueError("local identity requires allowed_origins")
        if type(self.enable_demo_adapter) is not bool:
            raise ValueError("enable_demo_adapter must be a bool")
        if type(self.enable_ark_adapter) is not bool:
            raise ValueError("enable_ark_adapter must be a bool")
        if self.enable_ark_adapter:
            if self.ark_models_config_path is None or self.ark_models_config_root is None:
                raise ValueError("Ark adapter requires an explicit administrator configuration")
            object.__setattr__(self, "ark_models_config_path", Path(self.ark_models_config_path))
            object.__setattr__(self, "ark_models_config_root", Path(self.ark_models_config_root).resolve(strict=False))
        object.__setattr__(self, "allowed_origins", tuple(dict.fromkeys(self.allowed_origins)))
        if self.services_config_path is not None:
            if not self.portal_base_url or self.services_config_root is None:
                raise ValueError("services configuration requires a Portal base URL and trusted root")
            object.__setattr__(self, "services_config_path", Path(self.services_config_path))
            object.__setattr__(self, "services_config_root", Path(self.services_config_root).resolve(strict=False))

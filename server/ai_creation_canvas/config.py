"""Configuration that keeps test and production state strictly separate."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from ai_creation_canvas.adapters.portal.catalog import ServiceDeclaration
from ai_creation_canvas.comfy.service import ComfyServiceDeclaration
from ai_creation_canvas.domain.models import ModelOperation


_PRODUCTION_PORTS = frozenset({9090, 8787, 8797, 8891, 8991})
_PRODUCTION_REPOSITORY = Path("/Users/260413a/ai-generation-portable-apps")
_REJECTED_TOKENS = frozenset({"default", "changeme", "change-me", "test"})
_MAX_SERVICES_BYTES = 65536
_DANGEROUS_FIELDS = frozenset({"base_url", "url", "script", "plugin", "code", "api_key", "token", "headers"})
_DNS_HOSTNAME = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*"
)


def load_comfyui_service_declarations(path: Path | str, expected_root: Path | str) -> tuple[ComfyServiceDeclaration, ...]:
    """Load a bounded server-only ComfyUI declaration file without following links."""
    root = Path(expected_root).resolve(strict=False)
    candidate = Path(path)
    try:
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_SERVICES_BYTES:
            raise ValueError
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("ComfyUI services configuration is invalid") from error
    if not isinstance(payload, dict) or set(payload) != {"services"} or not isinstance(payload["services"], list):
        raise ValueError("ComfyUI services configuration is invalid")
    declarations: list[ComfyServiceDeclaration] = []
    for item in payload["services"]:
        try:
            if not isinstance(item, dict) or set(item) != {"service_id", "base_url", "timeout_seconds", "auth_header_ref"}:
                raise ValueError
            service_id = item["service_id"]
            base_url = item["base_url"]
            timeout_seconds = item["timeout_seconds"]
            auth_header_ref = item["auth_header_ref"]
            if not isinstance(service_id, str) or not service_id.isascii() or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", service_id) is None:
                raise ValueError
            if not isinstance(base_url, str) or any(ord(char) <= 32 or ord(char) > 126 for char in base_url):
                raise ValueError
            parsed = urlsplit(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError
            port = parsed.port
            if port in _PRODUCTION_PORTS:
                raise ValueError
            if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
                raise ValueError
            if auth_header_ref is not None and (not isinstance(auth_header_ref, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", auth_header_ref)):
                raise ValueError
            declarations.append(ComfyServiceDeclaration(service_id, base_url.rstrip("/"), timeout_seconds, auth_header_ref))
        except (TypeError, ValueError) as error:
            raise ValueError("ComfyUI services configuration is invalid") from error
    if len({declaration.service_id for declaration in declarations}) != len(declarations):
        raise ValueError("ComfyUI services configuration is invalid")
    return tuple(declarations)


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


def _is_exact_trusted_host(value: object) -> bool:
    if not isinstance(value, str) or not value or value != value.strip() or any(char.isspace() for char in value):
        return False
    try:
        ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return bool(_DNS_HOSTNAME.fullmatch(value)) and not value.replace(".", "").isdigit()
    return True


def is_within_production_repository(path: Path | str) -> bool:
    """Return whether a path is contained by the protected production repository."""
    candidate = Path(path).expanduser().resolve(strict=False)
    production_repo = _PRODUCTION_REPOSITORY.resolve(strict=False)
    return _is_within(candidate, production_repo)


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
    credential_pools_path: Path | str | None = None
    credential_pools_root: Path | str | None = None
    asset_library_config_path: Path | str | None = None
    asset_library_config_root: Path | str | None = None
    portal_allow_loopback_http: bool = False
    portal_ca_file: Path | str | None = None
    portal_max_concurrency: int = 8
    identity_mode: str = "signed_portal"
    session_ttl_seconds: int = 12 * 60 * 60
    session_cookie_name: str = "aicc_session"
    allowed_origins: tuple[str, ...] = ()
    trusted_hosts: tuple[str, ...] = ()
    enable_demo_adapter: bool = False
    enable_ark_adapter: bool = False
    ark_models_config_path: Path | str | None = None
    ark_models_config_root: Path | str | None = None
    prompt_skill_model_id: str | None = None
    max_image_upload_bytes: int = 10 * 1024 * 1024
    max_video_upload_bytes: int = 64 * 1024 * 1024
    max_audio_upload_bytes: int = 32 * 1024 * 1024
    upload_concurrency: int = 4
    user_asset_quota_bytes: int = 2 * 1024 * 1024 * 1024
    total_asset_quota_bytes: int = 10 * 1024 * 1024 * 1024
    redis_url: str | None = None
    generation_global_concurrency: int = 8
    generation_provider_concurrency: int = 4
    generation_user_concurrency: int = 2
    comfyui_services_config_path: Path | str | None = None
    comfyui_services_config_root: Path | str | None = None

    def __post_init__(self) -> None:
        if self.environment not in {"test", "production", "development"}:
            raise ValueError("environment must be test, development, or production")
        if not isinstance(self.port, int) or isinstance(self.port, bool) or not 1 <= self.port <= 65535:
            raise ValueError("port must be a valid TCP port")
        data_dir = Path(self.data_dir).expanduser().resolve(strict=False)
        if (self.environment == "test" or (self.identity_mode == "local" and self.environment != "production")) and self.port in _PRODUCTION_PORTS:
            raise ValueError("non-production environment cannot use a production port")
        if self.environment != "production" and is_within_production_repository(data_dir):
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
            try:
                parsed = urlsplit(origin) if isinstance(origin, str) else None
                port = parsed.port if parsed is not None else None
            except ValueError:
                parsed = None
                port = None
            if (
                parsed is None
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or not parsed.hostname
                or "*" in parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or port == 0
                or "?" in origin
                or "#" in origin
                or any(char.isspace() for char in origin)
            ):
                raise ValueError("allowed_origins is invalid")
        if self.identity_mode == "local" and not self.allowed_origins:
            raise ValueError("local identity requires allowed_origins")
        if not isinstance(self.trusted_hosts, tuple) or len(self.trusted_hosts) > 16:
            raise ValueError("trusted_hosts is invalid")
        if not all(_is_exact_trusted_host(host) for host in self.trusted_hosts):
            raise ValueError("trusted_hosts is invalid")
        if type(self.enable_demo_adapter) is not bool:
            raise ValueError("enable_demo_adapter must be a bool")
        if type(self.enable_ark_adapter) is not bool:
            raise ValueError("enable_ark_adapter must be a bool")
        for field_name in ("max_image_upload_bytes", "max_video_upload_bytes", "max_audio_upload_bytes"):
            value = getattr(self, field_name)
            if type(value) is not int or not 1 <= value <= 2 * 1024 * 1024 * 1024:
                raise ValueError(f"{field_name} must be a positive bounded integer")
        if type(self.upload_concurrency) is not int or not 1 <= self.upload_concurrency <= 32:
            raise ValueError("upload_concurrency must be between 1 and 32")
        for field_name in ("user_asset_quota_bytes", "total_asset_quota_bytes"):
            value = getattr(self, field_name)
            if type(value) is not int or not 1 <= value <= 1024 * 1024 * 1024 * 1024:
                raise ValueError(f"{field_name} must be a positive bounded integer")
        if self.total_asset_quota_bytes < self.user_asset_quota_bytes:
            raise ValueError("total asset quota must not be smaller than user asset quota")
        if self.redis_url is not None:
            parsed_redis = urlsplit(self.redis_url)
            if parsed_redis.scheme not in {"redis", "rediss"} or not parsed_redis.hostname or parsed_redis.fragment:
                raise ValueError("redis_url must use redis:// or rediss://")
        for field_name in ("generation_global_concurrency", "generation_provider_concurrency", "generation_user_concurrency"):
            value = getattr(self, field_name)
            if type(value) is not int or not 1 <= value <= 32:
                raise ValueError(f"{field_name} must be between 1 and 32")
        if self.generation_provider_concurrency > self.generation_global_concurrency or self.generation_user_concurrency > self.generation_global_concurrency:
            raise ValueError("generation concurrency hierarchy is invalid")
        if self.enable_ark_adapter:
            if self.ark_models_config_path is None or self.ark_models_config_root is None:
                raise ValueError("Ark adapter requires an explicit administrator configuration")
            object.__setattr__(self, "ark_models_config_path", Path(self.ark_models_config_path))
            object.__setattr__(self, "ark_models_config_root", Path(self.ark_models_config_root).resolve(strict=False))
        if self.prompt_skill_model_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.prompt_skill_model_id):
            raise ValueError("prompt_skill_model_id is invalid")
        object.__setattr__(self, "allowed_origins", tuple(dict.fromkeys(self.allowed_origins)))
        trusted_hosts: list[str] = []
        seen_hosts: set[str] = set()
        for host in self.trusted_hosts:
            normalized_host = host.casefold()
            if normalized_host not in seen_hosts:
                trusted_hosts.append(normalized_host)
                seen_hosts.add(normalized_host)
        object.__setattr__(self, "trusted_hosts", tuple(trusted_hosts))
        if self.services_config_path is not None:
            if not self.portal_base_url or self.services_config_root is None:
                raise ValueError("services configuration requires a Portal base URL and trusted root")
            object.__setattr__(self, "services_config_path", Path(self.services_config_path))
            object.__setattr__(self, "services_config_root", Path(self.services_config_root).resolve(strict=False))
        if self.credential_pools_path is not None:
            if self.credential_pools_root is None:
                raise ValueError("credential pools path requires a trusted root")
            credential_pools_root = Path(self.credential_pools_root).expanduser().resolve(strict=False)
            credential_pools_path = Path(self.credential_pools_path).expanduser()
            try:
                path_metadata = credential_pools_path.lstat()
            except OSError:
                path_metadata = None
            if path_metadata is not None and (stat.S_ISLNK(path_metadata.st_mode) or not stat.S_ISREG(path_metadata.st_mode)):
                raise ValueError("credential pools path must be a regular non-symlink file")
            resolved_credential_pools_path = credential_pools_path.resolve(strict=False)
            if not _is_within(resolved_credential_pools_path, credential_pools_root):
                raise ValueError("credential pools path must resolve under trusted root")
            object.__setattr__(self, "credential_pools_path", credential_pools_path)
            object.__setattr__(self, "credential_pools_root", credential_pools_root)
        elif self.credential_pools_root is not None:
            raise ValueError("credential pools root requires a credential pools path")
        if self.asset_library_config_path is not None:
            if self.asset_library_config_root is None:
                raise ValueError("asset library config path requires a trusted root")
            asset_library_root = Path(self.asset_library_config_root).expanduser().resolve(strict=False)
            asset_library_path = Path(self.asset_library_config_path).expanduser()
            try:
                path_metadata = asset_library_path.lstat()
            except OSError:
                path_metadata = None
            if path_metadata is not None and (stat.S_ISLNK(path_metadata.st_mode) or not stat.S_ISREG(path_metadata.st_mode)):
                raise ValueError("asset library config path must be a regular non-symlink file")
            if not _is_within(asset_library_path.resolve(strict=False), asset_library_root):
                raise ValueError("asset library config path must resolve under trusted root")
            object.__setattr__(self, "asset_library_config_path", asset_library_path)
            object.__setattr__(self, "asset_library_config_root", asset_library_root)
        elif self.asset_library_config_root is not None:
            raise ValueError("asset library config root requires a config path")
        if self.comfyui_services_config_path is not None:
            if self.comfyui_services_config_root is None:
                raise ValueError("ComfyUI services configuration requires a trusted root")
            comfy_root = Path(self.comfyui_services_config_root).expanduser().resolve(strict=False)
            comfy_path = Path(self.comfyui_services_config_path).expanduser()
            try:
                metadata = comfy_path.lstat()
            except OSError:
                metadata = None
            if metadata is not None and (stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode)):
                raise ValueError("ComfyUI services configuration must be a regular non-symlink file")
            if not _is_within(comfy_path.resolve(strict=False), comfy_root):
                raise ValueError("ComfyUI services configuration must resolve under trusted root")
            object.__setattr__(self, "comfyui_services_config_path", comfy_path)
            object.__setattr__(self, "comfyui_services_config_root", comfy_root)
        elif self.comfyui_services_config_root is not None:
            raise ValueError("ComfyUI services configuration root requires a configuration path")

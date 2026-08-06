"""Configuration that keeps test and production state strictly separate."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


_PRODUCTION_PORTS = frozenset({9090, 8787, 8797, 8891, 8991})
_PRODUCTION_REPOSITORY = Path("/Users/260413a/ai-generation-portable-apps")
_REJECTED_TOKENS = frozenset({"default", "changeme", "change-me", "test"})


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

    def __post_init__(self) -> None:
        if self.environment not in {"test", "production", "development"}:
            raise ValueError("environment must be test, development, or production")
        if not isinstance(self.port, int) or isinstance(self.port, bool) or not 1 <= self.port <= 65535:
            raise ValueError("port must be a valid TCP port")
        data_dir = Path(self.data_dir).expanduser().resolve(strict=False)
        production_repo = _PRODUCTION_REPOSITORY.resolve(strict=False)
        if self.environment == "test" and self.port in _PRODUCTION_PORTS:
            raise ValueError("test environment cannot use a production port")
        if self.environment == "test" and _is_within(data_dir, production_repo):
            raise ValueError("test environment cannot use the production repository")
        if not isinstance(self.signature_ttl_seconds, int) or self.signature_ttl_seconds < 1:
            raise ValueError("signature_ttl_seconds must be positive")
        object.__setattr__(self, "data_dir", data_dir)
        object.__setattr__(self, "portal_internal_token", _validate_token(self.portal_internal_token))

"""Atomic administrator import for server-only credential pool JSON."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import stat

from ai_creation_canvas.credential_pools import (
    CredentialPoolLoader,
    CredentialPoolSnapshot,
    parse_credential_pool_json,
)
from ai_creation_canvas.trusted_routing import trusted_route_presets


@dataclass(frozen=True, slots=True)
class CredentialPoolImportResult:
    snapshot: CredentialPoolSnapshot
    safe_summaries: tuple[dict[str, object], ...]


def _invalid() -> ValueError:
    return ValueError("credential pools configuration is invalid")


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_target(target: Path, root: Path) -> tuple[Path, Path]:
    try:
        root = root.expanduser().resolve(strict=True)
        target = target.expanduser().absolute()
        parent = target.parent
        if parent.resolve(strict=True) != parent or not _inside(target, root):
            raise _invalid()
        root_meta = root.lstat()
        parent_meta = parent.lstat()
        current = target.lstat()
        if (
            stat.S_ISLNK(root_meta.st_mode)
            or stat.S_ISLNK(parent_meta.st_mode)
            or not stat.S_ISDIR(parent_meta.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or stat.S_IMODE(current.st_mode) & ~0o600
        ):
            raise _invalid()
        return target, parent
    except (OSError, ValueError):
        raise _invalid() from None


def _validate_trusted_pools(snapshot: CredentialPoolSnapshot) -> None:
    allowed = {
        ("chiyun-banana", "banana", "nano-banana"),
        ("chiyun-gpt-image2", "gpt-image", "gpt-image"),
        ("ark", "official", "seedream"),
        ("ark", "official", "seedance"),
    }
    trusted_provider_families = {(preset.provider_id, preset.family) for preset in trusted_route_presets().values()}
    for pool in snapshot.as_mapping().values():
        if any((pool.provider_id, pool.group, family) not in allowed for family in pool.allowed_families):
            raise ValueError("provider/group/family 组合不受支持，请对照示例文件")
        if any((pool.provider_id, family) not in trusted_provider_families for family in pool.allowed_families):
            raise ValueError("provider/group/family 组合不受支持，请对照示例文件")


def _write_exclusive(path: Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def import_credential_pool_json(
    loader: CredentialPoolLoader,
    target: Path,
    root: Path,
    raw: bytes,
) -> CredentialPoolImportResult:
    """Validate fully, then atomically replace the configured pool file."""
    try:
        candidate = parse_credential_pool_json(raw)
    except ValueError:
        raise ValueError("JSON 语法或字段有误，请对照示例文件") from None
    _validate_trusted_pools(candidate)
    try:
        target, parent = _validate_target(Path(target), Path(root))
    except ValueError:
        raise ValueError("服务器配置文件位置不安全，请联系技术人员") from None
    temporary = parent / f".{target.name}.{secrets.token_hex(12)}.import"
    try:
        _write_exclusive(temporary, raw)
        verified = CredentialPoolLoader(temporary, production=True).load()
        if verified.safe_summaries() != candidate.safe_summaries():
            raise _invalid()
        os.replace(temporary, target)
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        snapshot = loader.load()
        return CredentialPoolImportResult(snapshot, snapshot.safe_summaries())
    except (OSError, ValueError):
        raise _invalid() from None
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

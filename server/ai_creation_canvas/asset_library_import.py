"""Atomic administrator import for the Ark asset library configuration."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import stat

from ai_creation_canvas.asset_library_config import (
    AssetLibraryConfig,
    AssetLibraryConfigLoader,
    parse_asset_library_config_json,
)


def _invalid() -> ValueError:
    return ValueError("asset library configuration is invalid")


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


def _write_exclusive(path: Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def import_asset_library_config(
    loader: AssetLibraryConfigLoader,
    target: Path,
    root: Path,
    raw: bytes,
) -> AssetLibraryConfig:
    """Validate fully, then atomically replace the configured asset library file."""
    candidate = parse_asset_library_config_json(raw)
    target, parent = _validate_target(Path(target), Path(root))
    temporary = parent / f".{target.name}.{secrets.token_hex(12)}.import"
    try:
        _write_exclusive(temporary, raw)
        verified = AssetLibraryConfigLoader(temporary, production=True).load()
        if verified.safe_summary() != candidate.safe_summary():
            raise _invalid()
        os.replace(temporary, target)
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return loader.load()
    except (OSError, ValueError):
        raise _invalid() from None
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

"""Atomic administrator import for the Ark generation API key."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import stat

from ai_creation_canvas.ark_key_config import ArkKeyConfigLoader, parse_ark_key_config_json


def _invalid() -> ValueError:
    return ValueError("ark key configuration is invalid")


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


def import_ark_key_config(
    loader: ArkKeyConfigLoader,
    target: Path,
    root: Path,
    raw: bytes,
) -> str:
    """Validate fully, then atomically replace the configured Ark key file."""
    candidate = parse_ark_key_config_json(raw)
    target, parent = _validate_target(Path(target), Path(root))
    temporary = parent / f".{target.name}.{secrets.token_hex(12)}.import"
    try:
        _write_exclusive(temporary, raw)
        verified = ArkKeyConfigLoader(temporary).current_key()
        if verified != candidate.api_key:
            raise _invalid()
        os.replace(temporary, target)
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        current = loader.current_key()
        if current is None:
            raise _invalid()
        return current
    except (OSError, ValueError):
        raise _invalid() from None
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

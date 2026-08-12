from __future__ import annotations

from pathlib import Path

import pytest

from ai_creation_canvas.config import Settings


def test_credential_pool_path_is_optional_outside_managed_route_validation(tmp_path: Path) -> None:
    settings = Settings("development", 8992, tmp_path / "data", "local-secret")

    assert settings.credential_pools_path is None
    assert settings.credential_pools_root is None


def test_credential_pool_path_must_resolve_under_explicit_trusted_root(tmp_path: Path) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    path = root / "credential-pools.yaml"
    settings = Settings(
        "production", 8991, tmp_path / "data", "deployment-secret",
        credential_pools_path=path,
        credential_pools_root=root,
    )

    assert settings.credential_pools_path == path
    assert settings.credential_pools_root == root.resolve()

    with pytest.raises(ValueError, match="credential pools path must resolve under trusted root"):
        Settings(
            "production", 8991, tmp_path / "data", "deployment-secret",
            credential_pools_path=root / ".." / "outside.yaml",
            credential_pools_root=root,
        )


def test_credential_pool_path_requires_a_trusted_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="credential pools path requires a trusted root"):
        Settings(
            "production", 8991, tmp_path / "data", "deployment-secret",
            credential_pools_path=tmp_path / "credential-pools.yaml",
        )


def test_credential_pool_path_rejects_a_symlink_before_loader_receives_it(tmp_path: Path) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    target = root / "target.yaml"
    target.write_text("version: 1\npools: {}\n", encoding="utf-8")
    link = root / "credential-pools.yaml"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="credential pools path must be a regular non-symlink file"):
        Settings(
            "production", 8991, tmp_path / "data", "deployment-secret",
            credential_pools_path=link,
            credential_pools_root=root,
        )

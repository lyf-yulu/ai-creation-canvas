from __future__ import annotations

from pathlib import Path

import pytest

from ai_creation_canvas.config import Settings


def test_trusted_hosts_accepts_lan_hosts_and_casefold_deduplicates(tmp_path: Path) -> None:
    settings = Settings(
        "development",
        8992,
        tmp_path / "data",
        "local-secret",
        trusted_hosts=("Canvas.LAN", "192.168.1.25", "canvas.lan"),
    )

    assert settings.trusted_hosts == ("canvas.lan", "192.168.1.25")


@pytest.mark.parametrize(
    "trusted_hosts",
    [
        ("*.lan",),
        ("canvas.*",),
        ("http://canvas.lan",),
        ("canvas.lan:8992",),
        ("canvas.lan/path",),
        ("",),
        ("canvas lan",),
    ],
)
def test_trusted_hosts_rejects_non_host_values(tmp_path: Path, trusted_hosts: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="trusted_hosts is invalid"):
        Settings("development", 8992, tmp_path / "data", "local-secret", trusted_hosts=trusted_hosts)


@pytest.mark.parametrize(
    "origin",
    (
        "http://*.lan",
        "http://canvas.lan/path",
        "http://user@canvas.lan",
        "http://canvas.lan?next=/",
        "http://canvas.lan#fragment",
    ),
)
def test_allowed_origins_rejects_wildcards_paths_and_userinfo(tmp_path: Path, origin: str) -> None:
    with pytest.raises(ValueError, match="allowed_origins is invalid"):
        Settings("development", 8992, tmp_path / "data", "local-secret", allowed_origins=(origin,))


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

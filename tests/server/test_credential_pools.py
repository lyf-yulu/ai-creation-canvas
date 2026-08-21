from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from ai_creation_canvas.credential_pools import CredentialPoolLoader


def write_pool_file(directory: Path, *, mode: int = 0o600) -> Path:
    path = directory / "credential-pools.yaml"
    path.write_text(
        """version: 1
pools:
  t8-gemini:
    provider: t8star
    group: gemini
    allowed_families: [nano-banana]
    keys:
      - id: gemini-key-1
        api_key: secret-gemini
        max_concurrency: 2
  t8-cc:
    provider: t8star
    group: cc
    allowed_families: [claude]
    keys:
      - id: cc-key-1
        api_key: secret-cc
        max_concurrency: 1
""",
        encoding="utf-8",
    )
    path.chmod(mode)
    return path


def test_t8_groups_are_distinct_and_safe(tmp_path: Path) -> None:
    path = write_pool_file(tmp_path, mode=0o600)

    snapshot = CredentialPoolLoader(path, production=True).load()

    assert snapshot.get("t8-gemini").allowed_families == ("nano-banana",)
    assert snapshot.get("t8-cc").allowed_families == ("claude",)
    encoded = json.dumps(snapshot.safe_summaries())
    assert "secret-gemini" not in encoded and "gemini-key-1" not in encoded
    with pytest.raises(FrozenInstanceError):
        snapshot.get("t8-gemini").group = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "payload",
    [
        """version: 1
pools:
  t8-gemini:
    provider: t8star
    provider: duplicated
    group: gemini
    allowed_families: [nano-banana]
    keys: [{id: key-01, api_key: secret, max_concurrency: 1}]
""",
        """version: 1
pools:
  t8-gemini:
    provider: t8star
    group: gemini
    allowed_families: [nano-banana]
    keys: [{id: key-01, api_key: secret, max_concurrency: 1}]
    executable: import-me
""",
        """version: 1
pools:
  t8-gemini:
    provider: t8star
    group: gemini
    allowed_families: [nano-banana]
    keys:
      - {id: key-01, api_key: secret, max_concurrency: 1}
      - {id: key-01, api_key: another-secret, max_concurrency: 1}
""",
        """version: 1
pools:
  t8-gemini:
    <<: &pool
      provider: t8star
      group: gemini
      allowed_families: [nano-banana]
      keys: [{id: key-01, api_key: secret, max_concurrency: 1}]
""",
    ],
)
def test_loader_rejects_duplicate_or_unknown_fields(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "credential-pools.yaml"
    path.write_text(payload, encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(ValueError, match="credential pools configuration is invalid"):
        CredentialPoolLoader(path, production=True).load()


def test_loader_rejects_symlink_and_overly_broad_production_file(tmp_path: Path) -> None:
    path = write_pool_file(tmp_path, mode=0o600)
    link = tmp_path / "credential-pools-link.yaml"
    link.symlink_to(path)

    with pytest.raises(ValueError, match="credential pools configuration is invalid"):
        CredentialPoolLoader(link, production=True).load()

    path.chmod(0o644)
    with pytest.raises(ValueError, match="credential pools configuration is invalid"):
        CredentialPoolLoader(path, production=True).load()


@pytest.mark.parametrize("mode", [0o700, 0o4600])
def test_loader_rejects_production_file_mode_bits_outside_0600(tmp_path: Path, mode: int) -> None:
    path = write_pool_file(tmp_path, mode=mode)

    with pytest.raises(ValueError, match="credential pools configuration is invalid"):
        CredentialPoolLoader(path, production=True).load()


def test_reload_keeps_last_known_good_snapshot_when_candidate_is_invalid(tmp_path: Path) -> None:
    path = write_pool_file(tmp_path, mode=0o600)
    loader = CredentialPoolLoader(path, production=True)
    first = loader.load()
    path.write_text("version: 2\npools: {}\n", encoding="utf-8")
    path.chmod(0o600)

    retained = loader.reload()

    assert retained is first
    assert retained.get("t8-cc") is not None


def test_each_pool_has_its_own_canonical_revision_digest(tmp_path: Path) -> None:
    snapshot = CredentialPoolLoader(write_pool_file(tmp_path), production=True).load()

    gemini = snapshot.get("t8-gemini")
    cc = snapshot.get("t8-cc")
    assert gemini is not None and cc is not None
    assert len(gemini.revision_digest) == 64
    assert gemini.revision_digest != cc.revision_digest


@pytest.mark.parametrize(
    "keys_yaml",
    ["[]", "[" + ", ".join("{id: key-%02d, api_key: secret, max_concurrency: 1}" % index for index in range(65)) + "]"],
)
def test_loader_bounds_each_pool_to_one_through_sixty_four_keys(tmp_path: Path, keys_yaml: str) -> None:
    path = tmp_path / "credential-pools.yaml"
    path.write_text(
        f"""version: 1
pools:
  t8-gemini:
    provider: t8star
    group: gemini
    allowed_families: [nano-banana]
    keys: {keys_yaml}
""",
        encoding="utf-8",
    )
    path.chmod(0o600)

    with pytest.raises(ValueError, match="credential pools configuration is invalid"):
        CredentialPoolLoader(path, production=True).load()


@pytest.mark.parametrize("max_concurrency", [0, 65])
def test_loader_bounds_per_key_concurrency(tmp_path: Path, max_concurrency: int) -> None:
    path = tmp_path / "credential-pools.yaml"
    path.write_text(
        f"""version: 1
pools:
  t8-gemini:
    provider: t8star
    group: gemini
    allowed_families: [nano-banana]
    keys: [{{id: key-01, api_key: secret, max_concurrency: {max_concurrency}}}]
""",
        encoding="utf-8",
    )
    path.chmod(0o600)

    with pytest.raises(ValueError, match="credential pools configuration is invalid"):
        CredentialPoolLoader(path, production=True).load()

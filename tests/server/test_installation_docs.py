from __future__ import annotations

from pathlib import Path

from ai_creation_canvas.credential_pools import parse_credential_pool_json


ROOT = Path(__file__).parents[2]


def test_readme_links_portable_installation_guide_and_real_scripts_exist() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    guide = ROOT / "docs" / "installation.md"

    assert "docs/installation.md" in readme
    assert guide.is_file()
    content = guide.read_text(encoding="utf-8")
    for script in ("run-local.sh", "build-release.sh", "security-scan.sh"):
        assert (ROOT / "scripts" / script).is_file()
        assert f"scripts/{script}" in content
    for flag in ("--credential-pools", "--credential-pools-root", "--redis-url", "--static-dir", "--check-config"):
        assert flag in content


def test_published_json_example_uses_real_schema_and_placeholder_secrets_only() -> None:
    path = ROOT / "server" / "config" / "credential-pools.example.json"
    raw = path.read_bytes()
    snapshot = parse_credential_pool_json(raw)

    assert set(snapshot.as_mapping()) == {"banana-chiyun", "gpt-image2-chiyun", "gpt-image2-pindo", "seedream-ark", "seedance-ark"}
    lowered = raw.lower()
    assert lowered.count(b"replace-with-provider-key") == 5
    assert b"sk-" not in lowered and b"ark-" not in lowered
    for summary in snapshot.safe_summaries():
        assert summary["key_count"] == 1

from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "acceptance-real-media.sh"


def run_guard(tmp_path: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "AICC_ALLOW_PAID_ACCEPTANCE": "YES",
        "ARK_API_KEY": "test-only-never-sent",
        "AICC_ACCEPTANCE_GUARD_ONLY": "YES",
        "AICC_ACCEPTANCE_DATA": str(tmp_path / "brand-new-data"),
        "AICC_ACCEPTANCE_PORT": "8998",
        "AICC_ACCEPTANCE_IMAGE_COUNT": "1",
        "AICC_ACCEPTANCE_VIDEO_COUNT": "1",
        "AICC_ACCEPTANCE_IMAGE_MODEL_ID": "doubao-seedream-4-0-250828",
        "AICC_ACCEPTANCE_VIDEO_MODEL_ID": "doubao-seedance-2-0-260128",
        "AICC_ACCEPTANCE_MODELS_CONFIG": str(ROOT / "server/config/ark-models.example.json"),
        **overrides,
    }
    return subprocess.run(["sh", str(SCRIPT)], cwd=ROOT, env=environment, text=True, capture_output=True, check=False)


def test_paid_acceptance_guard_accepts_only_the_exact_bounded_slice(tmp_path: Path) -> None:
    result = run_guard(tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "Paid acceptance guard ready. No provider request was made."
    assert "test-only" not in result.stdout + result.stderr


def test_paid_acceptance_guard_requires_explicit_opt_in(tmp_path: Path) -> None:
    result = run_guard(tmp_path, AICC_ALLOW_PAID_ACCEPTANCE="NO")
    assert result.returncode == 64
    assert "AICC_ALLOW_PAID_ACCEPTANCE=YES" in result.stderr


def test_paid_acceptance_guard_rejects_wrong_model_or_more_than_one_call(tmp_path: Path) -> None:
    wrong_model = run_guard(tmp_path, AICC_ACCEPTANCE_VIDEO_MODEL_ID="unreviewed-model")
    assert wrong_model.returncode == 64
    assert "allowlist" in wrong_model.stderr
    too_many = run_guard(tmp_path, AICC_ACCEPTANCE_IMAGE_COUNT="2")
    assert too_many.returncode == 64
    assert "exactly one" in too_many.stderr


def test_paid_acceptance_guard_requires_a_new_data_directory_and_safe_port(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    reused = run_guard(tmp_path, AICC_ACCEPTANCE_DATA=str(existing))
    assert reused.returncode == 64
    assert "brand-new" in reused.stderr
    reserved = run_guard(tmp_path, AICC_ACCEPTANCE_PORT="8994")
    assert reserved.returncode == 64
    assert "reserved" in reserved.stderr


def test_paid_acceptance_runs_every_release_gate_before_the_client() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    client = source.index("acceptance_real_media.py")
    for gate in ("git diff --check", "security-scan.sh", "verify:release", "build-release.sh", "--skip-web-build"):
        assert 0 <= source.index(gate) < client

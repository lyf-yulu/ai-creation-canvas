from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "acceptance-real-media.sh"


def run_guard(tmp_path: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "AICC_RUN_PAID_ACCEPTANCE": "YES",
        "AICC_ACCEPTANCE_GUARD_ONLY": "YES",
        "AICC_ACCEPTANCE_DATA": str(tmp_path / "brand-new-data"),
        "AICC_ACCEPTANCE_PORT": "8998",
        "AICC_ACCEPTANCE_MODEL_IDS": "banana",
        "AICC_ACCEPTANCE_CHANNEL_IDS": "banana-chiyun",
        "AICC_ACCEPTANCE_BANANA_SAMPLE_COUNT": "0",
        "AICC_MAX_PAID_CALLS": "1",
        "AICC_CHIYUN_BASE_URL": "https://chiyun.example",
        "CHIYUN_API_KEY": "test-only-never-sent",
        **overrides,
    }
    for name in ("ARK_API_KEY", "T8STAR_API_KEY"):
        if name not in overrides:
            environment.pop(name, None)
    return subprocess.run(
        ["sh", str(SCRIPT)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_guard_only_accepts_one_explicit_channel_without_provider_io(tmp_path: Path) -> None:
    result = run_guard(tmp_path)

    assert result.returncode == 0
    assert "Paid acceptance guard ready. No provider request was made." in result.stdout
    assert "CHIYUN_API_KEY=SET" in result.stdout
    assert "T8STAR_API_KEY=UNSET" in result.stdout
    assert "ARK_API_KEY=UNSET" in result.stdout
    assert "test-only" not in result.stdout + result.stderr
    assert not (tmp_path / "brand-new-data").exists()


def test_guard_requires_the_new_exact_paid_opt_in(tmp_path: Path) -> None:
    missing = run_guard(tmp_path, AICC_RUN_PAID_ACCEPTANCE="")
    wrong = run_guard(tmp_path, AICC_RUN_PAID_ACCEPTANCE="yes")

    assert missing.returncode == 64
    assert wrong.returncode == 64
    assert "AICC_RUN_PAID_ACCEPTANCE=YES" in missing.stderr
    assert "AICC_RUN_PAID_ACCEPTANCE=YES" in wrong.stderr


def test_guard_rejects_missing_extra_or_unknown_models(tmp_path: Path) -> None:
    missing = run_guard(tmp_path, AICC_ACCEPTANCE_MODEL_IDS="")
    extra = run_guard(tmp_path, AICC_ACCEPTANCE_MODEL_IDS="banana,seedance")
    unknown = run_guard(tmp_path, AICC_ACCEPTANCE_MODEL_IDS="unreviewed")

    for result in (missing, extra, unknown):
        assert result.returncode == 64
        assert "model" in result.stderr.lower()


def test_guard_rejects_unknown_duplicate_or_uncovered_channels(tmp_path: Path) -> None:
    unknown = run_guard(tmp_path, AICC_ACCEPTANCE_CHANNEL_IDS="banana-unknown")
    duplicate = run_guard(tmp_path, AICC_ACCEPTANCE_CHANNEL_IDS="banana-chiyun,banana-chiyun")
    uncovered = run_guard(
        tmp_path,
        AICC_ACCEPTANCE_MODEL_IDS="banana,seedance",
        AICC_ACCEPTANCE_CHANNEL_IDS="banana-chiyun",
    )

    for result in (unknown, duplicate, uncovered):
        assert result.returncode == 64
        assert "channel" in result.stderr.lower() or "model" in result.stderr.lower()


def test_guard_enforces_total_paid_call_budget_between_one_and_twenty(tmp_path: Path) -> None:
    below = run_guard(tmp_path, AICC_MAX_PAID_CALLS="0")
    above = run_guard(tmp_path, AICC_MAX_PAID_CALLS="21")
    not_numeric = run_guard(tmp_path, AICC_MAX_PAID_CALLS="one")
    plan_exceeds_budget = run_guard(
        tmp_path,
        AICC_ACCEPTANCE_BANANA_SAMPLE_COUNT="1",
        AICC_MAX_PAID_CALLS="1",
    )

    for result in (below, above, not_numeric, plan_exceeds_budget):
        assert result.returncode == 64
        assert "AICC_MAX_PAID_CALLS" in result.stderr or "budget" in result.stderr.lower()


def test_guard_requires_only_the_selected_channel_credentials_and_origins(tmp_path: Path) -> None:
    missing_key = run_guard(tmp_path, CHIYUN_API_KEY="")
    missing_origin = run_guard(tmp_path, AICC_CHIYUN_BASE_URL="")
    t8_without_t8_key = run_guard(
        tmp_path,
        AICC_ACCEPTANCE_CHANNEL_IDS="banana-t8star",
        AICC_T8STAR_BASE_URL="https://t8star.example",
        T8STAR_API_KEY="",
    )

    assert missing_key.returncode == 64
    assert "CHIYUN_API_KEY" in missing_key.stderr
    assert missing_origin.returncode == 64
    assert "AICC_CHIYUN_BASE_URL" in missing_origin.stderr
    assert t8_without_t8_key.returncode == 64
    assert "T8STAR_API_KEY" in t8_without_t8_key.stderr


def test_guard_requires_a_brand_new_ignored_or_strictly_external_data_path(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()

    reused = run_guard(tmp_path, AICC_ACCEPTANCE_DATA=str(existing))
    rejected_repo_path = run_guard(tmp_path, AICC_ACCEPTANCE_DATA=str(ROOT / "work" / "paid-data"))
    accepted_ignored_path = run_guard(
        tmp_path,
        AICC_ACCEPTANCE_DATA=str(ROOT / ".paid-acceptance" / "guard-test-never-created"),
    )

    assert reused.returncode == 64
    assert "brand-new" in reused.stderr
    assert rejected_repo_path.returncode == 64
    assert "data path" in rejected_repo_path.stderr.lower()
    assert accepted_ignored_path.returncode == 0


def test_guard_rejects_traversal_and_symlinked_external_data_paths(tmp_path: Path) -> None:
    traversal = run_guard(
        tmp_path,
        AICC_ACCEPTANCE_DATA=str(ROOT / ".paid-acceptance" / ".." / "state" / "paid-data"),
    )
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    symlinked = run_guard(tmp_path, AICC_ACCEPTANCE_DATA=str(linked_parent / "data"))

    assert traversal.returncode == 64
    assert symlinked.returncode == 64
    assert "symlink" in symlinked.stderr.lower()


def test_offline_key_boundary_consumes_the_bundle_without_exposing_a_value(tmp_path: Path) -> None:
    result = run_guard(
        tmp_path,
        AICC_ACCEPTANCE_GUARD_ONLY="NO",
        AICC_ACCEPTANCE_ENV_PROBE="YES",
        CHIYUN_API_KEY="sentinel-paid-key-never-log",
    )

    assert result.returncode == 0
    assert "Paid acceptance key boundary ready. No provider request was made." in result.stdout
    assert "sentinel-paid-key" not in result.stdout + result.stderr


def test_paid_client_is_after_clean_worktree_and_every_offline_release_gate() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    client = source.rindex("acceptance_real_media.py")

    for gate in (
        "git diff --check",
        "git diff --exit-code",
        "git diff --cached --exit-code",
        "security-scan.sh",
        "pytest -q",
        "verify:release",
        "build-release.sh",
        "--skip-web-build",
    ):
        assert 0 <= source.index(gate) < client

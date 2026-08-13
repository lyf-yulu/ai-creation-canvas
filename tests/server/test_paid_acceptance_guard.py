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
        "AICC_ACCEPTANCE_DATA": str(ROOT / ".paid-acceptance" / f"guard-{tmp_path.name}"),
        "AICC_ACCEPTANCE_PORT": "8998",
        "AICC_ACCEPTANCE_MODEL_IDS": "seedream",
        "AICC_ACCEPTANCE_CHANNEL_IDS": "seedream-ark",
        "AICC_ACCEPTANCE_BANANA_SAMPLE_COUNT": "0",
        "AICC_MAX_PAID_CALLS": "1",
        "ARK_API_KEY": "test-only-never-sent",
        **overrides,
    }
    for name in ("CHIYUN_API_KEY", "T8STAR_API_KEY"):
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
    assert "CHIYUN_BANANA_API_KEY=UNSET" in result.stdout
    assert "CHIYUN_GPT_IMAGE2_API_KEY=UNSET" in result.stdout
    assert "T8STAR_API_KEY=UNSET" in result.stdout
    assert "ARK_API_KEY=SET" in result.stdout
    assert "test-only" not in result.stdout + result.stderr
    assert not (ROOT / ".paid-acceptance" / f"guard-{tmp_path.name}").exists()


def test_guard_accepts_only_the_exact_twelve_call_real_production_matrix(tmp_path: Path) -> None:
    result = run_guard(
        tmp_path,
        AICC_REAL_PRODUCTION_MATRIX="YES",
        AICC_ACCEPTANCE_MODEL_IDS="banana,gpt-image2,seedream,seedance",
        AICC_ACCEPTANCE_CHANNEL_IDS="banana-chiyun,gpt-image2-chiyun,seedream-ark,seedance-ark",
        AICC_ACCEPTANCE_BANANA_SAMPLE_COUNT="8",
        AICC_MAX_PAID_CALLS="12",
        CHIYUN_BANANA_API_KEY="test-only-banana-key",
        CHIYUN_GPT_IMAGE2_API_KEY="test-only-gpt-key",
    )
    assert result.returncode == 0, result.stderr
    assert "logical_jobs=12 provider_post_budget=12" in result.stdout
    assert "No provider request was made" in result.stdout


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
        AICC_ACCEPTANCE_MODEL_IDS="seedream,seedance",
        AICC_ACCEPTANCE_CHANNEL_IDS="seedream-ark,seedance-ark",
        AICC_MAX_PAID_CALLS="1",
    )

    for result in (below, above, not_numeric, plan_exceeds_budget):
        assert result.returncode == 64
        assert "AICC_MAX_PAID_CALLS" in result.stderr or "budget" in result.stderr.lower()


def test_guard_requires_selected_key_and_rejects_unapproved_third_party_origins(tmp_path: Path) -> None:
    missing_key = run_guard(tmp_path, ARK_API_KEY="")
    chiyun_unapproved = run_guard(
        tmp_path,
        AICC_ACCEPTANCE_MODEL_IDS="banana",
        AICC_ACCEPTANCE_CHANNEL_IDS="banana-chiyun",
        AICC_CHIYUN_BASE_URL="https://attacker.example",
        CHIYUN_BANANA_API_KEY="test-only-never-sent",
    )
    t8_unapproved = run_guard(
        tmp_path,
        AICC_ACCEPTANCE_MODEL_IDS="banana",
        AICC_ACCEPTANCE_CHANNEL_IDS="banana-t8star",
        AICC_T8STAR_BASE_URL="https://attacker.example",
        T8STAR_API_KEY="test-only-never-sent",
    )

    assert missing_key.returncode == 64
    assert "ARK_API_KEY" in missing_key.stderr
    assert chiyun_unapproved.returncode == 0
    assert t8_unapproved.returncode == 64
    assert "approved origin" in t8_unapproved.stderr.lower()


def test_guard_requires_a_brand_new_direct_child_of_the_repo_paid_root(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()

    reused = run_guard(tmp_path, AICC_ACCEPTANCE_DATA=str(existing))
    arbitrary_external = run_guard(tmp_path, AICC_ACCEPTANCE_DATA=str(tmp_path / "brand-new-external"))
    rejected_repo_path = run_guard(tmp_path, AICC_ACCEPTANCE_DATA=str(ROOT / "work" / "paid-data"))
    nested_paid_path = run_guard(tmp_path, AICC_ACCEPTANCE_DATA=str(ROOT / ".paid-acceptance" / "nested" / "paid-data"))
    production_project = run_guard(
        tmp_path,
        AICC_ACCEPTANCE_DATA="/Users/260413a/ai-generation-portable-apps/state/task4-paid-data",
    )
    accepted_ignored_path = run_guard(
        tmp_path,
        AICC_ACCEPTANCE_DATA=str(ROOT / ".paid-acceptance" / "guard-test-never-created"),
    )

    assert reused.returncode == 64
    assert "brand-new" in reused.stderr
    for result in (arbitrary_external, rejected_repo_path, nested_paid_path, production_project):
        assert result.returncode == 64
        assert "data path" in result.stderr.lower()
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
    foreign_key = tmp_path / "foreign-key-locator"
    foreign_pool = tmp_path / "foreign-pool-locator"
    foreign_key.write_text("must-survive", encoding="utf-8")
    foreign_pool.write_text("must-survive", encoding="utf-8")
    result = run_guard(
        tmp_path,
        AICC_ACCEPTANCE_GUARD_ONLY="NO",
        AICC_ACCEPTANCE_ENV_PROBE="YES",
        AICC_ACCEPTANCE_KEY_FILE=str(foreign_key),
        AICC_ACCEPTANCE_POOL_FILE=str(foreign_pool),
        AICC_CHIYUN_BASE_URL="https://attacker.example",
        AICC_T8STAR_BASE_URL="https://attacker.example",
        ARK_API_KEY="sentinel-paid-key-never-log",
    )

    assert result.returncode == 0
    assert "Paid acceptance key boundary ready. No provider request was made." in result.stdout
    assert "sentinel-paid-key" not in result.stdout + result.stderr
    assert foreign_key.read_text(encoding="utf-8") == "must-survive"
    assert foreign_pool.read_text(encoding="utf-8") == "must-survive"


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
        "npm audit",
        "build-release.sh",
        "--skip-web-build",
    ):
        assert 0 <= source.index(gate) < client


def test_paid_data_directory_is_created_relative_to_nofollow_directory_descriptors() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    creation = source.index("Securely create the already-validated paid data directory")
    client = source.rindex("acceptance_real_media.py")

    assert creation < client
    for token in ("os.O_NOFOLLOW", "dir_fd=repo_descriptor", "dir_fd=paid_descriptor", "os.fstat(data_descriptor)"):
        assert token in source[creation:client]


def test_shell_removes_only_the_exact_key_bundle_inode_it_created() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "unset AICC_ACCEPTANCE_KEY_FILE AICC_ACCEPTANCE_POOL_FILE AICC_CHIYUN_BASE_URL AICC_T8STAR_BASE_URL" in source
    for token in (
        "AICC_OWNED_KEY_PARENT_DEVICE",
        "AICC_OWNED_KEY_FILE_INODE",
        "os.stat(name, dir_fd=descriptor, follow_symlinks=False)",
        "os.unlink(name, dir_fd=descriptor)",
    ):
        assert token in source

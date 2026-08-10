from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_real_media_runner_fails_before_build_without_server_key(tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment.pop("ARK_API_KEY", None)
    result = subprocess.run(
        ["sh", str(ROOT / "scripts" / "run-real-media-local.sh")],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 64
    assert "ARK_API_KEY" in result.stderr

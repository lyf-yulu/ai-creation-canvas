"""Isolated contract checks for the Portal-to-Canvas thin integration.

These tests deliberately use a temporary, synthetic Portal source tree.  They
never start or contact a real Portal or any configured service port.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings


REPO_ROOT = Path(__file__).resolve().parents[2]
PREPARE = REPO_ROOT / "scripts" / "prepare-portal-test-copy.sh"
PATCH = REPO_ROOT / "integrations" / "portal" / "signed-identity-v2.patch"


def _signature(secret: str, *, timestamp: int, user_id: str, role: str, username: str) -> str:
    payload = f"v2\n{timestamp}\n{user_id}\n{role}\n{quote(username, safe='~-._')}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


class FixturePortal:
    """A local contract double: the browser cannot choose its Canvas identity."""

    def __init__(self, tmp_path: Path, user_id: str, role: str, username: str) -> None:
        self.secret = "fixture-signing-secret"
        self.user_id, self.role, self.username = user_id, role, username
        self.client = TestClient(
            create_app(Settings("test", 8992, tmp_path / user_id, self.secret)),
            raise_server_exceptions=False,
        )
        self.jobs: dict[str, str] = {}
        self.usage_events: list[tuple[str, str]] = []

    def _portal_headers(self, browser_headers: dict[str, str] | None = None) -> dict[str, str]:
        # This represents the thin proxy's explicit removal of every browser
        # identity/signature header before it mints a replacement identity.
        _ = {
            key: value
            for key, value in (browser_headers or {}).items()
            if not key.lower().startswith("x-portal-")
        }
        timestamp = int(time.time())
        return {
            "X-Portal-Sig-Version": "2",
            "X-Portal-Timestamp": str(timestamp),
            "X-Portal-User-Id": self.user_id,
            "X-Portal-Username": self.username,
            "X-Portal-Role": self.role,
            "X-Portal-Signature": _signature(
                self.secret, timestamp=timestamp, user_id=self.user_id, role=self.role, username=self.username
            ),
            "Cookie": "portal_session=fixture",
        }

    def get_canvas(self, path: str, browser_headers: dict[str, str] | None = None):
        assert path.startswith("/ai-canvas/")
        return self.client.get(path.removeprefix("/ai-canvas"), headers=self._portal_headers(browser_headers))

    def submit_canvas_job(self, job_id: str, job_type: str) -> str:
        self.jobs[job_id] = self.user_id
        self.usage_events.append((self.user_id, job_type))  # only the underlying Portal service records usage
        return job_id

    def result_for(self, job_id: str) -> str | None:
        return f"opaque-{job_id}" if self.jobs.get(job_id) == self.user_id else None


def _source_fixture(path: Path) -> None:
    path.mkdir()
    (path / "app.py").write_text(
        "from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get('/healthz')\ndef healthz():\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    (path / "app_spec.py").write_text("APP_NAME = 'fixture-portal'\n", encoding="utf-8")
    (path / "config.example.json").write_text('{"listen_port": 9090}\n', encoding="utf-8")
    (path / "static").mkdir()
    (path / "static" / "index.html").write_text("fixture", encoding="utf-8")
    (path / "state").mkdir()
    (path / "state" / "secret.db").write_text("do-not-copy", encoding="utf-8")
    (path / "seedance").mkdir()
    (path / "seedance" / "app.py").write_text("do-not-copy", encoding="utf-8")
    (path / "portal").mkdir()
    (path / "portal" / "core.py").write_text("safe = True\n", encoding="utf-8")
    (path / "portal" / "seedance_service.py").write_text("do-not-copy", encoding="utf-8")
    (path / "portal" / ".env.local").write_text("do-not-copy", encoding="utf-8")


def _run_prepare(source: Path, target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(PREPARE), str(source), str(target)], text=True, capture_output=True, check=False)


def test_copy_script_creates_only_safe_test_fixture_and_applies_patch(tmp_path):
    source = tmp_path / "portal-source"
    _source_fixture(source)
    target = REPO_ROOT / "work" / "portal-test-contract-copy"
    if target.exists():
        pytest.fail(f"test target unexpectedly exists: {target}")
    try:
        result = _run_prepare(source, target)
        assert result.returncode == 0, result.stderr
        assert (target / "app.py").read_text(encoding="utf-8").find("AI_CANVAS_MOUNT") >= 0
        assert not (target / "state").exists()
        assert not (target / "seedance").exists()
        assert (target / "portal" / "core.py").exists()
        assert not (target / "portal" / "seedance_service.py").exists()
        assert not (target / "portal" / ".env.local").exists()
        config = json.loads((target / "ai-canvas-test.json").read_text(encoding="utf-8"))
        assert config == {"canvas_origin": "http://127.0.0.1:8992", "portal_port": 9190, "test_data_dir": str(target / "test-data")}
    finally:
        if target.exists():
            subprocess.run(["rm", "-rf", str(target)], check=True)


def test_copy_script_rejects_existing_target_and_patch_mismatch(tmp_path):
    source = tmp_path / "portal-source"
    _source_fixture(source)
    target = REPO_ROOT / "work" / "portal-test-existing"
    target.mkdir(parents=True, exist_ok=False)
    try:
        result = _run_prepare(source, target)
        assert result.returncode != 0
        assert (target / "sentinel").exists() is False
    finally:
        target.rmdir()

    bad_source = tmp_path / "bad-portal-source"
    _source_fixture(bad_source)
    (bad_source / "app.py").write_text("unrecognized source shape\\n", encoding="utf-8")
    mismatch_target = REPO_ROOT / "work" / "portal-test-mismatch"
    try:
        result = _run_prepare(bad_source, mismatch_target)
        assert result.returncode != 0
        assert not mismatch_target.exists()
    finally:
        if mismatch_target.exists():
            subprocess.run(["rm", "-rf", str(mismatch_target)], check=True)


def test_copy_script_rejects_symlinked_allowlisted_input(tmp_path):
    source = tmp_path / "portal-source"
    _source_fixture(source)
    (source / "app.py").unlink()
    (source / "app.py").symlink_to(source / "state" / "secret.db")
    target = REPO_ROOT / "work" / "portal-test-symlink"
    try:
        result = _run_prepare(source, target)
        assert result.returncode != 0
        assert not target.exists()
    finally:
        if target.exists():
            subprocess.run(["rm", "-rf", str(target)], check=True)


def test_proxy_identity_is_signed_v2_and_browser_forgery_cannot_switch_user(tmp_path):
    portal = FixturePortal(tmp_path, "user-a", "user", "Alice Example")
    response = portal.get_canvas(
        "/ai-canvas/api/v1/session",
        {"X-Portal-User-Id": "user-b", "X-Portal-Role": "admin", "X-Portal-Signature": "forged"},
    )
    assert response.status_code == 200
    assert response.json() == {"user_id": "user-a", "username": "Alice Example", "role": "user"}

    signed = portal._portal_headers()
    signed["X-Portal-Username"] = "Mallory"
    assert portal.client.get("/api/v1/session", headers=signed).status_code == 401


def test_two_users_are_isolated_and_underlying_usage_is_counted_once(tmp_path):
    first = FixturePortal(tmp_path, "user-a", "user", "Alice")
    second = FixturePortal(tmp_path, "user-b", "viewer", "Bob")
    job = first.submit_canvas_job("job-a", "image")
    assert first.result_for(job) == "opaque-job-a"
    assert second.result_for(job) is None
    assert first.usage_events == [("user-a", "image")]
    assert second.usage_events == []


def test_patch_declares_a_fixed_mount_and_no_browser_selected_upstream_or_usage_header():
    content = PATCH.read_text(encoding="utf-8")
    assert 'AI_CANVAS_MOUNT = "/ai-canvas"' in content
    assert 'AI_CANVAS_ORIGIN = "http://127.0.0.1:8992"' in content
    assert "proxy_target" not in content
    assert "X-Job-Id" not in content
    assert "hmac.compare_digest" in content
    assert "X-Portal-Signature" in content

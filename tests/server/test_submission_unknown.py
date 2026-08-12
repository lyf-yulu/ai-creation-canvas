from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from tests.server.test_jobs_model_routes import ScriptedCoordinator, build_app, headers, payload
from ai_creation_canvas.coordination import CredentialLease


def test_credential_lease_repr_does_not_expose_key_identity_or_secret() -> None:
    rendered = repr(CredentialLease("route-a", "pool-a", "sensitive-key-id", "sensitive-secret", "fingerprint", "owner"))
    assert "sensitive-key-id" not in rendered
    assert "sensitive-secret" not in rendered


def test_possible_send_timeout_is_visible_unknown_and_never_replayed(tmp_path: Path) -> None:
    attempts = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("body timeout", request=request)
    app, store, _ = build_app(tmp_path, handler, ScriptedCoordinator())
    client = TestClient(app, raise_server_exceptions=False)

    first = client.post("/api/v1/jobs", headers=headers(), json=payload())
    repeated = client.post("/api/v1/jobs", headers=headers(), json=payload())
    conflict = client.post("/api/v1/jobs", headers=headers(), json=payload(prompt="different"))

    assert first.status_code == repeated.status_code == 201
    assert first.json() == repeated.json()
    assert first.json()["status"] == "submission_unknown"
    assert conflict.status_code == 409
    assert attempts == 1
    item, _ = store.job_for_owner(first.json()["id"], "user-a")
    assert item is not None and item["submission_state"] == "submission_unknown"
    assert item["submission_token"] is None and item["lease_until"] is None
    encoded = json.dumps(item, sort_keys=True)
    assert "secret-value" not in encoded and "gemini-a" not in encoded


def test_concurrent_identical_requests_reserve_once_and_submit_once(tmp_path: Path) -> None:
    attempts = 0
    barrier = threading.Barrier(2)
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"data": [{"b64_json": "iVBORw0KGgptYW5hZ2VkLXJlc3VsdA=="}]})
    app, store, _ = build_app(tmp_path, handler, ScriptedCoordinator())
    results: list[tuple[int, dict[str, object]]] = []
    def submit() -> None:
        with TestClient(app, raise_server_exceptions=False) as client:
            barrier.wait()
            response = client.post("/api/v1/jobs", headers=headers(), json=payload())
            results.append((response.status_code, response.json()))
    threads = [threading.Thread(target=submit), threading.Thread(target=submit)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()

    assert [status for status, _ in results] == [201, 201]
    assert len({body["id"] for _, body in results}) == 1
    assert attempts == 1
    with store._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM canvas_jobs").fetchone()[0] == 1

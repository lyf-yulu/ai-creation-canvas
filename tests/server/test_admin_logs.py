from __future__ import annotations

import logging
import os
import re

import pytest

from tests.server.test_model_assignments import ORIGIN, local_clients


_LINE = "2026-08-19T10:00:00,123456+0800 {level} probe.module {msg}"
_LINES = [
    _LINE.format(level="INFO", msg='127.0.0.1:54321 - "GET /api/v1/session HTTP/1.1" 200 OK'),
    _LINE.format(level="WARNING", msg="generation job polling failed transiently"),
    _LINE.format(level="ERROR", msg="unhandled request failure: POST /api/v1/jobs"),
    _LINE.format(level="ERROR", msg="Traceback (most recent call last):"),
    _LINE.format(level="ERROR", msg='  File "worker.py", line 12, in submit'),
    _LINE.format(level="INFO", msg="job completed"),
]


def _write_log(tmp_path, name: str, content: str):
    logs = tmp_path / "data" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / name
    path.write_text(content, encoding="utf-8")
    return logs, path


def test_log_files_empty_when_logs_dir_missing(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, user, admin_headers, user_headers

    response = admin.get("/api/v1/admin/logs/files")

    assert response.status_code == 200
    assert response.json() == {"files": []}


def test_log_files_lists_whitelisted_rotated_files_newest_first(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, user, admin_headers, user_headers

    logs, _ = _write_log(tmp_path, "server.log.1", "older backup\n")
    logs, _ = _write_log(tmp_path, "server.log", "current file\n")
    (logs / "other.txt").write_text("not a log\n", encoding="utf-8")
    (logs / ".hidden").write_text("hidden\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (logs / "server.log.2").symlink_to(outside)
    now = 1_700_000_000.0
    os.utime(logs / "server.log.1", (now, now))
    os.utime(logs / "server.log", (now + 60.0, now + 60.0))

    response = admin.get("/api/v1/admin/logs/files")

    assert response.status_code == 200
    files = response.json()["files"]
    assert [item["name"] for item in files] == ["server.log", "server.log.1"]
    assert set(files[0]) == {"name", "size", "mtime"}
    assert files[0]["size"] == len("current file\n")
    assert files[0]["mtime"] == pytest.approx(now + 60.0)


def test_log_content_returns_last_lines_in_chronological_order(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, user, admin_headers, user_headers

    _write_log(tmp_path, "server.log", "\n".join(_LINES) + "\n")

    response = admin.get("/api/v1/admin/logs/content", params={"file": "server.log", "lines": 3})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"file", "lines", "window_total", "truncated", "log_lines"}
    assert body["file"] == "server.log"
    assert body["lines"] == 3
    assert body["window_total"] == 3
    assert body["truncated"] is False
    assert body["log_lines"] == _LINES[-3:]


def test_log_content_lines_one_and_partial_trailing_line_preserved(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, user, admin_headers, user_headers

    _write_log(tmp_path, "server.log", "first\nsecond\npartial-tail")

    response = admin.get("/api/v1/admin/logs/content", params={"file": "server.log", "lines": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["log_lines"] == ["second", "partial-tail"]

    response = admin.get("/api/v1/admin/logs/content", params={"file": "server.log", "lines": 1})
    assert response.json()["log_lines"] == ["partial-tail"]


def test_log_content_level_filter_keeps_severity_and_traceback_lines(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, user, admin_headers, user_headers

    _write_log(tmp_path, "server.log", "\n".join(_LINES) + "\n")

    response = admin.get("/api/v1/admin/logs/content", params={"file": "server.log", "lines": 10, "level": "WARNING"})

    assert response.status_code == 200
    body = response.json()
    assert body["window_total"] == 6
    assert body["log_lines"] == _LINES[1:5]

    response = admin.get("/api/v1/admin/logs/content", params={"file": "server.log", "lines": 10, "level": "ERROR"})
    assert response.json()["log_lines"] == _LINES[2:5]


def test_log_content_keyword_filter_is_case_insensitive(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, user, admin_headers, user_headers

    _write_log(tmp_path, "server.log", "\n".join(_LINES) + "\n")

    response = admin.get("/api/v1/admin/logs/content", params={"file": "server.log", "lines": 10, "q": "POLLING"})

    assert response.status_code == 200
    assert response.json()["log_lines"] == [_LINES[1]]


def test_log_content_rejects_unknown_or_unsafe_files(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, user, admin_headers, user_headers

    logs, _ = _write_log(tmp_path, "server.log", "one\n")
    (logs / "other.txt").write_text("not a log\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (logs / "server.log.2").symlink_to(outside)

    for params in (
        {"file": "missing.log"},
        {"file": "other.txt"},
        {"file": "server.log.2"},
        {"file": "../server.log"},
        {"file": "..%2Fserver.log"},
        {"file": "/etc/passwd"},
        {"file": ""},
    ):
        response = admin.get("/api/v1/admin/logs/content", params=params)
        assert response.status_code == 404, params


def test_log_content_rejects_out_of_range_params(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, user, admin_headers, user_headers

    _write_log(tmp_path, "server.log", "one\n")

    for params in (
        {"file": "server.log", "lines": 0},
        {"file": "server.log", "lines": 99999},
        {"file": "server.log", "level": "VERBOSE"},
        {"file": "server.log", "q": "x" * 201},
    ):
        response = admin.get("/api/v1/admin/logs/content", params=params)
        assert response.status_code == 400, params


def test_log_content_truncated_flag_when_window_hits_byte_budget(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, user, admin_headers, user_headers

    long_lines = [_LINE.format(level="INFO", msg=f"filler {index:05d} " + "y" * 500_000) for index in range(10)]
    _write_log(tmp_path, "server.log", "\n".join(long_lines) + "\n")

    response = admin.get("/api/v1/admin/logs/content", params={"file": "server.log", "lines": 2000})

    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is True
    assert 0 < body["window_total"] < 10


def test_log_download_returns_attachment_with_file_bytes(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, user, admin_headers, user_headers

    content = "downloaded log\nsecond line\n"
    _, path = _write_log(tmp_path, "server.log", content)

    response = admin.get("/api/v1/admin/logs/download", params={"file": "server.log"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "attachment" in response.headers["content-disposition"]
    assert 'filename="server.log"' in response.headers["content-disposition"]
    assert response.content == path.read_bytes()


def test_log_download_snapshots_content_when_the_log_grows_during_streaming(tmp_path) -> None:
    import asyncio

    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del accounts, user, admin_headers, user_headers

    # The live log gains access lines while being served, which used to push
    # a FileResponse past its stat-time content-length and abort the transfer
    # (h11 LocalProtocolError). Drive the ASGI app directly so the append can
    # land right after the response starts, inside the body-streaming window.
    content = ("growing log line\n" * 2_000).encode("utf-8")
    _, path = _write_log(tmp_path, "server.log", content.decode("utf-8"))
    session_cookie = admin.cookies.get("aicc_session")
    assert session_cookie

    async def drive() -> bytes:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/admin/logs/download",
            "raw_path": b"/api/v1/admin/logs/download?file=server.log",
            "query_string": b"file=server.log",
            "headers": [(b"host", b"testserver"), (b"origin", ORIGIN.encode()), (b"cookie", f"aicc_session={session_cookie}".encode())],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 80),
        }
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.start":
                with open(path, "ab") as handle:
                    handle.write(b"appended while streaming\n" * 1_000)

        await app(scope, receive, send)
        return b"".join(message["body"] for message in messages if message["type"] == "http.response.body")

    assert asyncio.run(drive()) == content


def test_log_download_rejects_traversal(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, user, admin_headers, user_headers

    _write_log(tmp_path, "server.log", "one\n")

    response = admin.get("/api/v1/admin/logs/download", params={"file": "..%2Fcanvas.sqlite3"})
    assert response.status_code == 404


def test_log_endpoints_are_admin_only(tmp_path) -> None:
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts, admin_headers

    _write_log(tmp_path, "server.log", "one\n")

    assert user.get("/api/v1/admin/logs/files").status_code == 404
    assert user.get("/api/v1/admin/logs/content", params={"file": "server.log"}).status_code == 404
    assert user.get("/api/v1/admin/logs/download", params={"file": "server.log"}).status_code == 404


def test_configure_logging_writes_timestamped_lines_for_every_physical_line(tmp_path) -> None:
    import ai_creation_canvas.logging_setup as module

    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    module._configured = False
    new_handlers: list[logging.Handler] = []
    try:
        path = module.configure_logging(tmp_path)
        assert path is not None
        new_handlers = [handler for handler in root.handlers if handler not in saved_handlers]
        probe = logging.getLogger("probe.logger")
        probe.info("hello world")
        probe.error("boom\nsecond line")
        for handler in new_handlers:
            handler.flush()

        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert len(lines) == 3
        for line in lines:
            assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2},\d{6}[+-]\d{4} (DEBUG|INFO|WARNING|ERROR|CRITICAL) probe\.logger ", line), line
        assert "hello world" in lines[0]
        assert "second line" in lines[2]
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        for handler in new_handlers:
            handler.close()
        module._configured = False


def test_configure_logging_degrades_to_console_when_logs_dir_unwritable(tmp_path) -> None:
    import ai_creation_canvas.logging_setup as module

    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    module._configured = False
    new_handlers: list[logging.Handler] = []
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    try:
        path = module.configure_logging(blocker / "logs")
        assert path is None
        new_handlers = [handler for handler in root.handlers if handler not in saved_handlers]
        assert new_handlers, "console handler should still be attached"
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        for handler in new_handlers:
            handler.close()
        module._configured = False

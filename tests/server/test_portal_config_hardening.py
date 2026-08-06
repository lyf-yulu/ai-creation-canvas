from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_creation_canvas.adapters.portal.client import PortalClient
from ai_creation_canvas.config import Settings, load_service_declarations


@pytest.mark.parametrize("url", ["https://portal.test\n.evil", "https://portal.test\t", "https://portal.test\r", "https://portal.test\x00", "https://例子.test"])
def test_portal_base_url_rejects_controls_and_unicode_host(url):
    with pytest.raises(ValueError):
        PortalClient(url, allowed_mounts=("/image",))


@pytest.mark.parametrize("value", ["true", 1, None])
def test_loopback_http_flag_requires_actual_bool(value):
    with pytest.raises(ValueError):
        PortalClient("http://127.0.0.1", allowed_mounts=("/image",), allow_loopback_http=value)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Settings("test", 8992, Path("/tmp/data"), "test-secret", portal_allow_loopback_http=value)  # type: ignore[arg-type]


def test_repository_service_example_loads():
    root = Path(__file__).parents[2] / "server" / "config"
    assert len(load_service_declarations(root / "services.example.json", root)) == 3


def test_loader_rejects_duplicate_mount_and_operation(tmp_path):
    payload = {"services": [
        {"service_id": "one", "mount": "/same", "service_type": "image", "operations": ["image.generate", "image.generate"]},
        {"service_id": "two", "mount": "/same", "service_type": "video", "operations": ["video.generate"]},
    ]}
    path = tmp_path / "services.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_service_declarations(path, tmp_path)

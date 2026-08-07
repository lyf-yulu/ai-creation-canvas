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


@pytest.mark.parametrize("payload", [
    {"services":[{"service_id":"a","mount":"/a","service_type":"image","operations":["image.generate"]},{"service_id":"a","mount":"/b","service_type":"video","operations":["video.generate"]}]},
    {"services":[{"service_id":"a","mount":"/a","service_type":"image","operations":["image.generate"]},{"service_id":"b","mount":"/a","service_type":"video","operations":["video.generate"]}]},
    {"services":[{"service_id":"a","mount":"/a","service_type":"image","operations":["image.generate","image.generate"]}]},
    {"services":[{"service_id":"a","mount":"/a","service_type":"image","operations":["image.generate"],"url":"x"}]},
    {"services":[{"service_id":"bad!","mount":"/a","service_type":"image","operations":["image.generate"]}]},
    {"services":[{"service_id":"a","mount":"/../a","service_type":"image","operations":["image.generate"]}]},
    {"services":[{"service_id":"a","mount":"/a","service_type":"bad","operations":["image.generate"]}]},
    {"services":[{"service_id":"a","mount":"/a","service_type":"image","operations":[]}]},
    {"services":"bad"}, [], {"services":["bad"]}, {"services":[{"service_id":"a"}]},
])
def test_loader_rejects_each_invalid_rule_independently(tmp_path, payload):
    path = tmp_path / "services.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="services configuration is invalid"):
        load_service_declarations(path, tmp_path)


@pytest.mark.parametrize("value", ["", 1, "é", "bad!", "a" * 65])
def test_loader_rejects_each_invalid_service_id(tmp_path, value):
    path=tmp_path/'x.json'; path.write_text(json.dumps({"services":[{"service_id":value,"mount":"/a","service_type":"image","operations":["image.generate"]}]}))
    with pytest.raises(ValueError, match="^services configuration is invalid$"): load_service_declarations(path,tmp_path)


@pytest.mark.parametrize("value", [None, "a", "/../a", "/a\n", "/%2e%2e/a"])
def test_loader_rejects_each_invalid_mount(tmp_path, value):
    path=tmp_path/'x.json'; path.write_text(json.dumps({"services":[{"service_id":"a","mount":value,"service_type":"image","operations":["image.generate"]}]}))
    with pytest.raises(ValueError, match="^services configuration is invalid$"): load_service_declarations(path,tmp_path)


def test_loader_rejects_symlink_directory_oversize_json_and_utf8(tmp_path):
    target=tmp_path/'target'; target.write_text('{}'); link=tmp_path/'link'; link.symlink_to(target)
    for path in (link, tmp_path):
        with pytest.raises(ValueError,match="^services configuration is invalid$"): load_service_declarations(path,tmp_path)
    big=tmp_path/'big'; big.write_bytes(b'x'*65537)
    bad=tmp_path/'bad'; bad.write_bytes(b'\xff')
    for path in (big,bad):
        with pytest.raises(ValueError,match="^services configuration is invalid$"): load_service_declarations(path,tmp_path)

def test_loader_rejects_malformed_json_with_fixed_safe_error(tmp_path):
    path=tmp_path/'bad.json'; path.write_text('{"services": [}', encoding='utf-8')
    with pytest.raises(ValueError) as error: load_service_declarations(path,tmp_path)
    assert str(error.value) == 'services configuration is invalid'

def test_loader_rejects_benign_unknown_entry_field_with_fixed_safe_error(tmp_path):
    path=tmp_path/'unknown.json'; path.write_text(json.dumps({"services":[{"service_id":"a","mount":"/a","service_type":"image","operations":["image.generate"],"description":"safe"}]}))
    with pytest.raises(ValueError) as error: load_service_declarations(path,tmp_path)
    assert str(error.value) == 'services configuration is invalid'

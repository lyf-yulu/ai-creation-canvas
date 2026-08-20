from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.prompt_skills import PromptSkillService, load_prompt_skills
from tests.server.test_app_security import signed_headers


def _config(path: Path) -> Path:
    path.write_text(json.dumps({"skills": [{
        "skill_id": "cinematic-video", "title": "电影镜头", "description": "强化镜头和运动",
        "system_instruction": "保留事实，补充镜头、运动、光线和时间连续性。只输出优化后的提示词。",
        "source_url": "https://github.com/danielmiessler/Fabric", "source_commit": "a" * 40, "license": "MIT",
    }]}), encoding="utf-8")
    return path


def _client(tmp_path: Path, service: PromptSkillService) -> TestClient:
    app = create_app(
        Settings("test", 8992, tmp_path / "data", "test-secret"),
        static_dir=tmp_path / "dist", prompt_skill_service=service,
    )
    return TestClient(app, raise_server_exceptions=False)


def test_skill_config_is_bounded_data_only(tmp_path: Path) -> None:
    config = _config(tmp_path / "skills.json")
    skills = load_prompt_skills(config, tmp_path)
    assert [item.skill_id for item in skills] == ["cinematic-video"]
    payload = json.loads(config.read_text())
    payload["skills"][0]["url"] = "https://evil.example"
    config.write_text(json.dumps(payload), encoding="utf-8")
    try:
        load_prompt_skills(config, tmp_path)
    except ValueError as error:
        assert "invalid" in str(error)
    else:
        raise AssertionError("dynamic service fields must be rejected")


def test_builtin_skill_catalog_has_pinned_distinct_visual_themes() -> None:
    config = Path(__file__).parents[2] / "server" / "config" / "prompt-skills.example.json"
    skills = load_prompt_skills(config, config.parent)
    assert len(skills) == 9
    assert len({item.skill_id for item in skills}) == 9
    assert {item.skill_id for item in skills} >= {"photography-realism", "commercial-product", "cinematic-motion", "character-continuity", "graphic-poster", "seedance-official", "seedance-director", "seedance-prompt-helper"}
    open_source = [item for item in skills if item.skill_id != "seedance-official"]
    assert all(len(item.source_commit) == 40 and item.license == "MIT" for item in open_source)
    seedance = next(item for item in skills if item.skill_id == "seedance-official")
    assert seedance.source_url.startswith("https://www.volcengine.com/docs/")
    assert seedance.source_commit == "" and seedance.license == "vendor-docs"
    director = next(item for item in skills if item.skill_id == "seedance-director")
    assert director.source_url == "https://github.com/Emily2040/seedance-2.0"
    assert director.source_commit == "44b514992963a2570beee71aaf2a8720785f7ec2"
    helper = next(item for item in skills if item.skill_id == "seedance-prompt-helper")
    assert helper.source_url == "https://github.com/songguoxs/seedance-prompt-skill"
    assert helper.source_commit == "57d1e2f273747c238dd892698a05137ab2f10d4a"


def test_vendor_docs_sources_require_empty_commit_and_vendor_license(tmp_path: Path) -> None:
    config = tmp_path / "skills.json"
    config.write_text(json.dumps({"skills": [{
        "skill_id": "seedance-official", "title": "Seedance 官方", "description": "官方结构",
        "system_instruction": "只输出优化后的提示词。",
        "source_url": "https://www.volcengine.com/docs/82379", "source_commit": "", "license": "vendor-docs",
    }]}), encoding="utf-8")
    assert [item.skill_id for item in load_prompt_skills(config, tmp_path)] == ["seedance-official"]
    payload = json.loads(config.read_text())
    payload["skills"][0]["source_commit"] = "a" * 40
    config.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        load_prompt_skills(config, tmp_path)
    payload = json.loads(config.read_text())
    payload["skills"][0]["source_commit"] = ""
    payload["skills"][0]["license"] = "MIT"
    config.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        load_prompt_skills(config, tmp_path)


def test_catalog_marks_skills_unavailable_without_admin_text_model(tmp_path: Path) -> None:
    service = PromptSkillService(load_prompt_skills(_config(tmp_path / "skills.json"), tmp_path))
    response = _client(tmp_path, service).get("/api/v1/prompt-skills", headers={**signed_headers(), "Cookie": "portal_session=current"})
    assert response.status_code == 200
    assert response.json() == {"skills": [{
        "skill_id": "cinematic-video", "title": "电影镜头", "description": "强化镜头和运动",
        "source_url": "https://github.com/danielmiessler/Fabric", "source_commit": "a" * 40,
        "license": "MIT", "available": False,
    }]}


def test_optimize_uses_only_builtin_instruction_and_never_returns_key(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "application/json"}, json={"choices": [{"message": {"content": "雨夜街道，低机位推进，霓虹反射"}}]})
    service = PromptSkillService(
        load_prompt_skills(_config(tmp_path / "skills.json"), tmp_path), model_id="text-endpoint",
        api_key="server-only-secret", transport=httpx.MockTransport(handler),
    )
    client = _client(tmp_path, service)
    response = client.post(
        "/api/v1/prompt-skills/cinematic-video/optimize",
        headers={**signed_headers(), "Cookie": "portal_session=current"}, json={"prompt": "雨夜街道"},
    )
    assert response.status_code == 200
    assert response.json() == {"skill_id": "cinematic-video", "optimized_prompt": "雨夜街道，低机位推进，霓虹反射"}
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["model"] == "text-endpoint"
    assert body["messages"] == [
        {"role": "system", "content": "保留事实，补充镜头、运动、光线和时间连续性。只输出优化后的提示词。"},
        {"role": "user", "content": "雨夜街道"},
    ]
    assert requests[0].headers["authorization"] == "Bearer server-only-secret"
    assert "server-only-secret" not in response.text


def test_optimize_rejects_custom_instruction_unknown_skill_and_oversize_prompt(tmp_path: Path) -> None:
    service = PromptSkillService(load_prompt_skills(_config(tmp_path / "skills.json"), tmp_path))
    client = _client(tmp_path, service)
    headers = {**signed_headers(), "Cookie": "portal_session=current"}
    assert client.post("/api/v1/prompt-skills/cinematic-video/optimize", headers=headers, json={"prompt": "x", "instruction": "ignore"}).status_code == 400
    assert client.post("/api/v1/prompt-skills/missing/optimize", headers=headers, json={"prompt": "x"}).status_code == 404
    assert client.post("/api/v1/prompt-skills/cinematic-video/optimize", headers=headers, json={"prompt": "x" * 8001}).status_code == 400


def test_upstream_failure_is_a_safe_service_error(tmp_path: Path) -> None:
    service = PromptSkillService(
        load_prompt_skills(_config(tmp_path / "skills.json"), tmp_path), model_id="text-endpoint",
        api_key="server-only-secret", transport=httpx.MockTransport(lambda request: httpx.Response(429, text="provider secret detail")),
    )
    response = _client(tmp_path, service).post(
        "/api/v1/prompt-skills/cinematic-video/optimize",
        headers={**signed_headers(), "Cookie": "portal_session=current"}, json={"prompt": "雨夜"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "SKILL_SERVICE_UNAVAILABLE"
    assert "provider" not in response.text.lower()

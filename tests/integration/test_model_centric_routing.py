from __future__ import annotations

import asyncio
import base64
import json
import struct
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.factory import ProviderProtocol, RouteAdapterFactory
from ai_creation_canvas.adapters.ark import _local_asset_loader
from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.auth.local import BootstrapResult
from ai_creation_canvas.catalog import ManagedRoutingRuntime
from ai_creation_canvas.config import Settings
from ai_creation_canvas.coordination import RedisExecutionCoordinator
from ai_creation_canvas.credential_pools import CredentialKey, CredentialPool
from ai_creation_canvas.domain.models import PortalRole
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.model_registry import ProviderDefinition
from ai_creation_canvas.model_registry import OperationContract
from ai_creation_canvas.model_routing import ModelRouteDefinition
from ai_creation_canvas.routing import RouteSelector
from ai_creation_canvas.storage.sqlite import CanvasStore
from scripts.acceptance_real_media import reference_png
from tests.server.test_route_key_coordination import ScriptRedis


ORIGIN = "http://127.0.0.1:46108"
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"
PNG = reference_png()
REFERENCE_ONE = b"\x89PNG\r\n\x1a\n" + b"offline-reference-one" + PNG_END
REFERENCE_TWO = b"\x89PNG\r\n\x1a\n" + b"offline-reference-two" + PNG_END


def assert_decodable_png(content: bytes) -> None:
    """Assert the provider fixture fulfils the image result wire contract."""
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert content[12:16] == b"IHDR"
    width, height = struct.unpack(">II", content[16:24])
    assert width > 0 and height > 0
    assert (width, height) == (64, 64)
    assert b"IDAT" in content
    assert content.endswith(PNG_END)


def image_contract() -> dict[str, object]:
    return {
        "operation": "image.edit",
        "input_ports": [
            {"port_id": "prompt", "media_type": "text", "min_items": 1, "max_items": 1},
            {"port_id": "reference_images", "media_type": "image", "min_items": 1, "max_items": 10},
        ],
        "output_media_type": "image",
        "parameter_schema": {"type": "object", "properties": {
            "size": {"type": "string", "enum": ["auto", "1024x1024"], "default": "auto"},
            "output_count": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
        }, "required": ["size", "output_count"], "additionalProperties": False},
        "parameter_mappings": {"size": "size", "output_count": "n"},
    }


def model_body() -> dict[str, object]:
    return {
        "model_id": "nano-banana",
        "display_name": "Nano Banana",
        "introduction": "Offline multi-reference image model.",
        "modality": "image",
        "operation_contracts": [image_contract()],
        "enabled": True,
    }


def route_body(route_id: str, provider_id: str, pool_id: str, *, priority: int = 1) -> dict[str, object]:
    return {
        "route_id": route_id,
        "model_id": "nano-banana",
        "provider_id": provider_id,
        "provider_model_name": "gemini-2.5-flash-image",
        "adapter_type": "chiyun_openai_images",
        "credential_pool_ref": pool_id,
        "family": "nano-banana",
        "operation_contracts": [image_contract()],
        "priority": priority,
        "max_concurrency": 4,
        "enabled": True,
    }


def pool(pool_id: str, provider_id: str, group: str, families: tuple[str, ...], key_ids: tuple[str, ...]) -> CredentialPool:
    return CredentialPool(
        pool_id,
        provider_id,
        group,
        families,
        tuple(CredentialKey(key_id, f"offline-fixture-secret-{key_id}", 1) for key_id in key_ids),
        __import__("hashlib").sha256(pool_id.encode()).hexdigest(),
    )


@dataclass
class AcceptanceApp:
    app: object
    store: CanvasStore
    accounts: BootstrapResult
    redis: ScriptRedis
    pools: dict[str, CredentialPool]


def build_acceptance_app(tmp_path: Path, provider: httpx.MockTransport) -> AcceptanceApp:
    store = CanvasStore(tmp_path / "acceptance-data")
    store.create_provider_definition(
        ProviderDefinition("chiyun", "Chiyun", "chiyun_openai_images", "https://google.example", "deployment-only"),
        actor_user_id="bootstrap",
    )
    store.create_provider_definition(
        ProviderDefinition("t8star", "T8Star", "chiyun_openai_images", "https://t8.example", "deployment-only"),
        actor_user_id="bootstrap",
    )
    store.create_provider_definition(
        ProviderDefinition("ark-video", "Ark Video", "ark", "https://ark.cn-beijing.volces.com", "deployment-only"),
        actor_user_id="bootstrap",
    )
    pools = {
        "banana-official": pool("banana-official", "chiyun", "official", ("nano-banana",), ("official-a", "official-b")),
        "banana-t8-gemini": pool("banana-t8-gemini", "t8star", "gemini", ("nano-banana",), ("gemini-a",)),
        "seedance-offline": pool("seedance-offline", "ark-video", "official", ("seedance",), ("seedance-a",)),
        "t8-cc": pool("t8-cc", "t8star", "cc", ("claude",), ("cc-a",)),
    }
    redis = ScriptRedis()
    coordinator = RedisExecutionCoordinator(
        redis,
        namespace="aicc-offline-acceptance",
        global_limit=16,
        provider_limit=8,
        user_limit=4,
        lease_seconds=30,
        credential_hmac_key=b"offline-acceptance-hmac-only",
    )
    factory = RouteAdapterFactory(
        data_dir=store.data_dir,
        asset_loader=_local_asset_loader(store.data_dir),
        provider_protocols={
            "chiyun": ProviderProtocol.from_readonly_deployment("chiyun", "chiyun_openai_images", "https://google.example", approved_origin="https://google.example"),
            "t8star": ProviderProtocol.from_readonly_deployment("t8star", "chiyun_openai_images", "https://t8.example", approved_origin="https://t8.example"),
            "ark-video": ProviderProtocol.from_readonly_deployment("ark-video", "ark", "https://ark.cn-beijing.volces.com", approved_origin="https://ark.cn-beijing.volces.com"),
        },
        transport=provider,
        trusted_route_validator=lambda _route: None,
    )
    runtime = ManagedRoutingRuntime(store, lambda: pools, RouteSelector(), coordinator, factory)
    registry = AdapterRegistry()
    app = create_app(
        Settings("test", 46108, store.data_dir, "offline-signing-only", identity_mode="local", allowed_origins=(ORIGIN,)),
        static_dir=tmp_path / "missing-dist",
        canvas_store=store,
        registry=registry,
        model_catalog=ModelCatalog(registry),
        managed_routing_runtime=runtime,
    )
    accounts = app.state.local_auth.bootstrap_accounts(())
    assert accounts.admin is not None and accounts.user is not None
    app.state.local_auth.create_user("canvas-other", "Other User", "offline-other-password", PortalRole.USER, must_change_password=False)
    return AcceptanceApp(app, store, accounts, redis, pools)


def test_acceptance_fixture_exposes_an_offline_seedance_route_pool(tmp_path: Path) -> None:
    environment = build_acceptance_app(tmp_path, httpx.MockTransport(lambda _request: httpx.Response(500)))
    provider = environment.store.provider_definition("ark-video")
    assert provider is not None and provider.adapter_type == "ark"
    seedance = environment.pools["seedance-offline"]
    assert seedance.provider_id == "ark-video"
    assert seedance.allowed_families == ("seedance",)


def login_and_change(client: TestClient, username: str, password: str) -> dict[str, str]:
    first = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert first.status_code == 200, first.text
    csrf = first.json()["csrf_token"]
    changed = client.post(
        "/api/v1/auth/change-password",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json={"current_password": password, "new_password": f"changed-{username}-offline-password"},
    )
    assert changed.status_code == 200, changed.text
    return {"Origin": ORIGIN, "X-CSRF-Token": changed.json()["csrf_token"]}


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


def configure_model(admin: TestClient, headers: dict[str, str], accounts: BootstrapResult) -> None:
    assert admin.post("/api/v1/admin/logical-models", headers=headers, json=model_body()).status_code == 201
    store = admin.app.state.canvas_store
    for body in (
        route_body("banana-official-route", "chiyun", "banana-official", priority=1),
        route_body("banana-t8-gemini-route", "t8star", "banana-t8-gemini", priority=2),
    ):
        store.create_model_route(ModelRouteDefinition(
            body["route_id"], body["model_id"], body["provider_id"], body["provider_model_name"],
            body["adapter_type"], body["credential_pool_ref"], body["family"],
            tuple(OperationContract.from_dict(item) for item in body["operation_contracts"]),
            body["priority"], body["max_concurrency"], enabled=body["enabled"],
        ), actor_user_id=accounts.admin.user_id)
    rejected = admin.post(
        "/api/v1/admin/logical-models/nano-banana/routes",
        headers=headers,
        json=route_body("banana-t8-cc-route", "t8star", "t8-cc", priority=0),
    )
    assert rejected.status_code == 400
    assert accounts.user is not None
    granted = admin.put(
        f"/api/v1/admin/users/{accounts.user.user_id}/models",
        headers=headers,
        json={"model_ids": ["nano-banana"]},
    )
    assert granted.status_code == 200, granted.text


def upload_reference(client: TestClient, headers: dict[str, str], name: str, content: bytes) -> str:
    response = client.post(
        "/api/v1/assets",
        headers=headers,
        files={"file": (name, content, "image/png")},
        data={"kind": "reference", "media_type": "image"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["asset_id"])


def test_model_centric_offline_route_rotation_idempotency_and_owner_isolation(tmp_path: Path) -> None:
    provider_calls: list[httpx.Request] = []

    def provider(request: httpx.Request) -> httpx.Response:
        provider_calls.append(request)
        if request.headers["authorization"].endswith("official-a"):
            return httpx.Response(429, json={"error": "fixture-capacity"})
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    environment = build_acceptance_app(tmp_path, httpx.MockTransport(provider))
    admin = TestClient(environment.app, base_url=ORIGIN)
    user = TestClient(environment.app, base_url=ORIGIN)
    other = TestClient(environment.app, base_url=ORIGIN)
    admin_headers = login_and_change(admin, environment.accounts.admin_username, environment.accounts.admin_password)
    user_headers = login_and_change(user, environment.accounts.user_username, environment.accounts.user_password)
    other_headers = login(other, "canvas-other", "offline-other-password")
    configure_model(admin, admin_headers, environment.accounts)

    pool_response = admin.get("/api/v1/admin/credential-pools")
    assert pool_response.status_code == 200
    pool_json = pool_response.text.lower()
    assert {item["pool_id"] for item in pool_response.json()["pools"]} == set(environment.pools)
    for forbidden in ("offline-fixture-secret", "official-a", "official-b", "gemini-a", "cc-a"):
        assert forbidden not in pool_json

    public = user.get("/api/v1/models")
    assert public.status_code == 200
    assert [item["model_id"] for item in public.json()["models"]] == ["nano-banana"]
    for forbidden in ("route_id", "provider_id", "credential", "pool", "group", "key"):
        assert forbidden not in public.text.lower()

    first_asset = upload_reference(user, user_headers, "first.png", REFERENCE_ONE)
    second_asset = upload_reference(user, user_headers, "second.png", REFERENCE_TWO)
    body = {
        "operation": "image.edit",
        "model_id": "nano-banana",
        "prompt": "offline acceptance prompt",
        "params": {"size": "1024x1024", "output_count": 1},
        "asset_ids": [],
        "inputs": {"reference_images": [first_asset, second_asset]},
        "idempotency_key": "offline-equal-request",
    }

    async def concurrent_submit() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=environment.app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, cookies=user.cookies) as first, httpx.AsyncClient(
            transport=transport, base_url=ORIGIN, cookies=user.cookies
        ) as second:
            return tuple(await asyncio.gather(
                first.post("/api/v1/jobs", headers=user_headers, json=body),
                second.post("/api/v1/jobs", headers=user_headers, json=body),
            ))  # type: ignore[return-value]

    created, repeated = asyncio.run(concurrent_submit())
    assert created.status_code == 201 and repeated.status_code == 201
    assert created.json()["id"] == repeated.json()["id"]
    assert len(provider_calls) == 2
    assert provider_calls[0].headers["authorization"].endswith("official-a")
    assert provider_calls[1].headers["authorization"].endswith("official-b")
    assert b'name="image[]"' in provider_calls[1].content
    assert provider_calls[1].content.index(REFERENCE_ONE) < provider_calls[1].content.index(REFERENCE_TWO)

    job_id = created.json()["id"]
    stored, forbidden = environment.store.job_for_owner(job_id, environment.accounts.user.user_id)
    assert not forbidden and stored is not None
    assert stored["route_id"] == "banana-official-route"
    assert stored["pool_revision_digest"] == environment.pools["banana-official"].revision_digest
    assert stored["submission_state"] == "submitted"
    encoded_snapshot = json.dumps(stored, sort_keys=True)
    for forbidden_value in ("offline-fixture-secret", "official-a", "official-b", "gemini-a", "cc-a"):
        assert forbidden_value not in encoded_snapshot
    assert "banana-t8-cc-route" not in encoded_snapshot

    completed = user.get(f"/api/v1/jobs/{job_id}")
    assert completed.status_code == 200 and completed.json()["status"] == "succeeded"
    assert other.get(f"/api/v1/jobs/{job_id}").status_code == 404
    result_url = f"/api/v1/results/{job_id}/0"
    assert other.get(result_url).status_code == 404
    head = user.head(result_url)
    assert head.status_code == 200
    assert head.headers["content-type"] == "image/png"
    ranged = user.get(result_url, headers={"Range": "bytes=0-7"})
    assert ranged.status_code == 206 and ranged.content == PNG[:8]
    full_result = user.get(result_url)
    assert full_result.status_code == 200
    assert full_result.headers["content-type"] == "image/png"
    assert full_result.content == PNG
    assert_decodable_png(full_result.content)

    revoked = admin.put(
        f"/api/v1/admin/users/{environment.accounts.user.user_id}/models",
        headers=admin_headers,
        json={"model_ids": []},
    )
    assert revoked.status_code == 200
    assert user.get("/api/v1/models").json()["models"] == []
    denied = user.post("/api/v1/jobs", headers=user_headers, json={**body, "idempotency_key": "offline-after-revoke"})
    assert denied.status_code == 400
    assert len(provider_calls) == 2
    assert user.get(result_url).status_code == 200

    recorded = json.dumps(environment.redis.recorded_commands)
    for forbidden_value in ("offline acceptance prompt", "nano-banana", "banana-official", "official-a", "offline-fixture-secret"):
        assert forbidden_value not in recorded


def test_ambiguous_provider_outcome_is_not_replayed(tmp_path: Path) -> None:
    provider_calls = 0

    def provider(request: httpx.Request) -> httpx.Response:
        nonlocal provider_calls
        provider_calls += 1
        raise httpx.ReadTimeout("fixture response became ambiguous", request=request)

    environment = build_acceptance_app(tmp_path, httpx.MockTransport(provider))
    admin = TestClient(environment.app, base_url=ORIGIN)
    user = TestClient(environment.app, base_url=ORIGIN)
    admin_headers = login_and_change(admin, environment.accounts.admin_username, environment.accounts.admin_password)
    user_headers = login_and_change(user, environment.accounts.user_username, environment.accounts.user_password)
    configure_model(admin, admin_headers, environment.accounts)
    asset_id = upload_reference(user, user_headers, "reference.png", REFERENCE_ONE)
    body = {
        "operation": "image.edit",
        "model_id": "nano-banana",
        "prompt": "ambiguous fixture prompt",
        "params": {"size": "auto", "output_count": 1},
        "asset_ids": [],
        "inputs": {"reference_images": [asset_id]},
        "idempotency_key": "offline-ambiguous-request",
    }

    first = user.post("/api/v1/jobs", headers=user_headers, json=body)
    repeated = user.post("/api/v1/jobs", headers=user_headers, json=body)

    assert first.status_code == 201 and repeated.status_code == 201
    assert first.json()["id"] == repeated.json()["id"]
    assert first.json()["status"] == "submission_unknown"
    stored, forbidden = environment.store.job_for_owner(first.json()["id"], environment.accounts.user.user_id)
    assert not forbidden and stored is not None and stored["submission_state"] == "submission_unknown"
    assert provider_calls == 1


def test_explicit_capacity_can_fall_through_to_compatible_gemini_route_but_never_cc(tmp_path: Path) -> None:
    provider_calls: list[httpx.Request] = []

    def provider(request: httpx.Request) -> httpx.Response:
        provider_calls.append(request)
        if "google.example" in str(request.url):
            return httpx.Response(429, json={"error": "fixture-capacity"})
        assert "t8.example" in str(request.url)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    environment = build_acceptance_app(tmp_path, httpx.MockTransport(provider))
    admin = TestClient(environment.app, base_url=ORIGIN)
    user = TestClient(environment.app, base_url=ORIGIN)
    admin_headers = login_and_change(admin, environment.accounts.admin_username, environment.accounts.admin_password)
    user_headers = login_and_change(user, environment.accounts.user_username, environment.accounts.user_password)
    configure_model(admin, admin_headers, environment.accounts)
    asset_id = upload_reference(user, user_headers, "reference.png", REFERENCE_ONE)
    created = user.post(
        "/api/v1/jobs",
        headers=user_headers,
        json={
            "operation": "image.edit",
            "model_id": "nano-banana",
            "prompt": "compatible route fixture",
            "params": {"size": "auto", "output_count": 1},
            "asset_ids": [],
            "inputs": {"reference_images": [asset_id]},
            "idempotency_key": "offline-compatible-route",
        },
    )

    assert created.status_code == 201
    completed = user.get(f"/api/v1/jobs/{created.json()['id']}")
    assert completed.status_code == 200 and completed.json()["status"] == "succeeded"
    assert len(provider_calls) == 3
    assert [str(request.url.host) for request in provider_calls] == ["google.example", "google.example", "t8.example"]
    assert provider_calls[-1].headers["authorization"].endswith("gemini-a")
    stored, forbidden = environment.store.job_for_owner(created.json()["id"], environment.accounts.user.user_id)
    assert not forbidden and stored is not None
    assert stored["route_id"] == "banana-t8-gemini-route"
    assert stored["pool_revision_digest"] == environment.pools["banana-t8-gemini"].revision_digest
    assert "t8-cc" not in str(stored["route_snapshot_json"])
    assert all(not request.headers["authorization"].endswith("cc-a") for request in provider_calls)

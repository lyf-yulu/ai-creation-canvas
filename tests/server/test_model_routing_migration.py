from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from ai_creation_canvas.domain.models import ModelInputPort, ModelOperation
from ai_creation_canvas.model_registry import OperationContract
from ai_creation_canvas.storage.sqlite import CanvasStore


_CREATED_AT = "2026-08-01T00:00:00+00:00"
_UPDATED_AT = "2026-08-02T00:00:00+00:00"


def _legacy_contracts() -> str:
    contract = OperationContract(
        ModelOperation.IMAGE_EDIT,
        (
            ModelInputPort("prompt", "text", 1, 1),
            ModelInputPort("reference_images", "image", 1, 10),
        ),
        "image",
        {
            "type": "object",
            "properties": {"size": {"type": "string"}},
            "additionalProperties": False,
        },
        {"size": "size"},
    )
    return json.dumps([contract.to_dict()], sort_keys=True, separators=(",", ":"))


def _seed_legacy_database(data_dir: Path, *, model_id: str = "chiyun-gpt-image-2") -> None:
    data_dir.mkdir(exist_ok=True)
    database = data_dir / "canvas.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            CREATE TABLE canvas_users (
                user_id TEXT PRIMARY KEY, username_normalized TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
                role TEXT NOT NULL, enabled INTEGER NOT NULL, must_change_password INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE canvas_providers (
                provider_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                adapter_type TEXT NOT NULL, base_url TEXT NOT NULL,
                credential_ref TEXT NOT NULL, enabled INTEGER NOT NULL,
                revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE canvas_models (
                model_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL REFERENCES canvas_providers(provider_id),
                provider_model_name TEXT NOT NULL, display_name TEXT NOT NULL, introduction TEXT NOT NULL,
                modality TEXT NOT NULL, operation_contracts_json TEXT NOT NULL, enabled INTEGER NOT NULL,
                revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE canvas_model_access (
                user_id TEXT NOT NULL REFERENCES canvas_users(user_id),
                model_id TEXT NOT NULL REFERENCES canvas_models(model_id),
                granted_by TEXT NOT NULL, granted_at TEXT NOT NULL, revoked_at TEXT,
                PRIMARY KEY(user_id, model_id)
            );
            """
        )
        db.execute(
            "INSERT INTO canvas_users VALUES (?,?,?,?,?,?,?,?,?)",
            ("user-1", "user", "User", "hash", "user", 1, 0, _CREATED_AT, _UPDATED_AT),
        )
        db.execute(
            "INSERT INTO canvas_providers VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "chiyun",
                "Chiyun",
                "chiyun_openai_images",
                "https://chiyun.example",
                "chiyun-primary",
                1,
                3,
                _CREATED_AT,
                _UPDATED_AT,
            ),
        )
        db.execute(
            "INSERT INTO canvas_models VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                model_id,
                "chiyun",
                "gpt-image-2",
                "GPT Image 2",
                "Multi-reference edit",
                "image",
                _legacy_contracts(),
                1,
                4,
                _CREATED_AT,
                _UPDATED_AT,
            ),
        )
        db.execute(
            "INSERT INTO canvas_model_access VALUES (?,?,?,?,NULL)",
            ("user-1", model_id, "admin-1", _CREATED_AT),
        )


def _routing_dump(database: Path) -> bytes:
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        body = {
            "logical": [dict(row) for row in db.execute("SELECT * FROM canvas_logical_models ORDER BY model_id")],
            "routes": [dict(row) for row in db.execute("SELECT * FROM canvas_model_routes ORDER BY route_id")],
            "marker": db.execute(
                "SELECT value FROM canvas_meta WHERE key='model_routing_legacy_migration_v1'"
            ).fetchone()[0],
        }
    return json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def test_additive_migration_preserves_old_tables_access_and_chiyun_contract(tmp_path: Path) -> None:
    _seed_legacy_database(tmp_path)

    store = CanvasStore(tmp_path)

    with sqlite3.connect(store.database) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        logical = db.execute(
            "SELECT model_id,display_name,modality,operation_contracts_json,enabled,archived_at,revision "
            "FROM canvas_logical_models"
        ).fetchone()
        route = db.execute(
            "SELECT route_id,model_id,provider_id,provider_model_name,adapter_type,credential_pool_ref,family,revision "
            "FROM canvas_model_routes"
        ).fetchone()
        access = db.execute("SELECT user_id,model_id,revoked_at FROM canvas_model_access").fetchall()
        job_columns = {row[1] for row in db.execute("PRAGMA table_info(canvas_jobs)")}

    assert {"canvas_models", "canvas_model_access", "canvas_logical_models", "canvas_model_routes"} <= tables
    assert logical[:3] == ("chiyun-gpt-image-2", "GPT Image 2", "image")
    assert json.loads(logical[3])[0]["operation"] == "image.edit"
    assert logical[4:] == (1, None, 4)
    assert route == (
        "legacy-chiyun-gpt-image-2",
        "chiyun-gpt-image-2",
        "chiyun",
        "gpt-image-2",
        "chiyun_openai_images",
        "chiyun-primary",
        "gpt-image-2",
        4,
    )
    assert access == [("user-1", "chiyun-gpt-image-2", None)]
    assert "route_id" in job_columns


def test_repeated_startup_is_byte_stable_and_marker_blocks_later_legacy_rows(tmp_path: Path) -> None:
    _seed_legacy_database(tmp_path)
    first = CanvasStore(tmp_path)
    before = _routing_dump(first.database)

    CanvasStore(tmp_path)
    after = _routing_dump(first.database)
    assert after == before

    with sqlite3.connect(first.database) as db:
        db.execute(
            "INSERT INTO canvas_models VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "created-after-marker",
                "chiyun",
                "gpt-image-3",
                "Later",
                "Not part of initialization migration",
                "image",
                _legacy_contracts(),
                1,
                1,
                _CREATED_AT,
                _UPDATED_AT,
            ),
        )

    CanvasStore(tmp_path)
    with sqlite3.connect(first.database) as db:
        assert db.execute(
            "SELECT 1 FROM canvas_logical_models WHERE model_id='created-after-marker'"
        ).fetchone() is None


def test_long_legacy_model_id_gets_stable_bounded_hashed_route_id(tmp_path: Path) -> None:
    model_id = "m" * 128
    _seed_legacy_database(tmp_path, model_id=model_id)

    first = CanvasStore(tmp_path)
    with sqlite3.connect(first.database) as db:
        route_id = db.execute("SELECT route_id FROM canvas_model_routes").fetchone()[0]
    CanvasStore(tmp_path)
    with sqlite3.connect(first.database) as db:
        repeated = db.execute("SELECT route_id FROM canvas_model_routes").fetchone()[0]

    assert route_id == repeated
    assert route_id.startswith("legacy-")
    assert len(route_id) == 71
    assert len(route_id) <= 128

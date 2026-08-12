from __future__ import annotations

import pytest

from ai_creation_canvas.config import Settings


def test_governed_models_require_redis_in_production_app_wiring(tmp_path):
    from ai_creation_canvas.app import create_app
    from ai_creation_canvas.storage.sqlite import CanvasStore
    from tests.server.test_model_registry import _model, _provider

    store = CanvasStore(tmp_path / "data")
    store.create_provider_definition(_provider(), actor_user_id="admin")
    store.create_model_definition(_model(), actor_user_id="admin")
    with pytest.raises(ValueError, match="Redis"):
        create_app(Settings("production", 8991, store.data_dir, "deployment-token"), static_dir=tmp_path / "dist", canvas_store=store)


def test_redis_settings_are_bounded_and_require_a_redis_scheme(tmp_path):
    settings = Settings("test", 8992, tmp_path / "data", "secret", redis_url="redis://127.0.0.1:6379/0", generation_global_concurrency=8, generation_provider_concurrency=4, generation_user_concurrency=2)
    assert settings.redis_url == "redis://127.0.0.1:6379/0"
    for changes in ({"redis_url": "http://127.0.0.1"}, {"generation_global_concurrency": 0}, {"generation_user_concurrency": 33}):
        with pytest.raises(ValueError):
            Settings("test", 8992, tmp_path / "other", "secret", **changes)

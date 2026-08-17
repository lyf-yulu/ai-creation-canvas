from __future__ import annotations

from pathlib import Path

import pytest

from ai_creation_canvas.storage.sqlite import CanvasStore


def new_store(tmp_path: Path) -> CanvasStore:
    return CanvasStore(tmp_path / "data")


def reserve(store: CanvasStore, asset_id: str, user_id: str = "user-a") -> None:
    store.create_asset(
        asset_id=asset_id, user_id=user_id, kind="library", media_type="image",
        mime_type="image/png", relative_path=f"assets/{asset_id}.png", size_bytes=16, status="processing",
    )


def test_library_asset_finalize_requires_ark_video_service_and_asset_id(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    reserve(store, "lib-1")

    row = store.finalize_library_asset("lib-1", service_id="ark-video", upstream_asset_id="asset-abcdef", status="active")
    assert row["upstream_asset_id"] == "asset-abcdef" and row["status"] == "active"
    with pytest.raises(KeyError):
        store.finalize_library_asset("lib-1", service_id="ark-video", upstream_asset_id="asset-ghijkl", status="active")
    with pytest.raises(ValueError, match="library finalization is invalid"):
        store.finalize_library_asset("lib-2", service_id="other", upstream_asset_id="asset-abcdef", status="active")
    with pytest.raises(ValueError, match="library finalization is invalid"):
        store.finalize_library_asset("lib-2", service_id="ark-video", upstream_asset_id="../../etc/passwd", status="active")
    with pytest.raises(ValueError, match="library finalization is invalid"):
        store.finalize_library_asset("lib-2", service_id="ark-video", upstream_asset_id="asset-abcdef", status="weird")


def test_library_recovery_journal_reapplies(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    reserve(store, "lib-1")
    store.record_library_finalize_recovery("lib-1", upstream_asset_id="asset-abcdef", status="active")

    reopened = CanvasStore(tmp_path / "data")

    row, _ = reopened.asset_for_owner("lib-1", "user-a")
    assert row is not None and row["upstream_asset_id"] == "asset-abcdef" and row["status"] == "active"
    assert not list(reopened.assets_dir.glob(".ark-library-recovery-*.pending"))


def test_stale_library_reservations_fail_on_startup(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    reserve(store, "lib-1")

    reopened = CanvasStore(tmp_path / "data")

    row, _ = reopened.asset_for_owner("lib-1", "user-a")
    assert row is not None and row["status"] == "failed" and row["upstream_asset_id"] is None


def test_default_group_id_round_trips_in_canvas_meta(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    assert store.ark_library_group_id() is None
    store.set_ark_library_group_id("asset-grp-1")
    assert store.ark_library_group_id() == "asset-grp-1"
    with pytest.raises(ValueError, match="library group id is invalid"):
        store.set_ark_library_group_id("../../etc/passwd")

    reopened = CanvasStore(tmp_path / "data")
    assert reopened.ark_library_group_id() == "asset-grp-1"


def test_delete_reserved_library_asset_only_removes_unfinalized(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    reserve(store, "lib-1")

    assert store.delete_reserved_library_asset("lib-1", "user-a") is True
    assert store.asset_for_owner("lib-1", "user-a") == (None, False)
    assert store.delete_reserved_library_asset("lib-1", "user-a") is False
    assert store.delete_reserved_library_asset("lib-1", "user-b") is False

    reserve(store, "lib-2")
    store.finalize_library_asset("lib-2", service_id="ark-video", upstream_asset_id="asset-abcdef", status="active")
    assert store.delete_reserved_library_asset("lib-2", "user-a") is False


def test_list_library_assets_for_owner_lists_only_library(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    reserve(store, "lib-1", "user-a")
    reserve(store, "lib-2", "user-a")
    reserve(store, "lib-b", "user-b")
    store.create_asset(
        asset_id="ref-1", user_id="user-a", kind="reference", media_type="image",
        mime_type="image/png", relative_path="assets/ref-1.png", size_bytes=16,
    )

    assets = store.list_library_assets_for_owner("user-a")
    assert [item["asset_id"] for item in assets] == ["lib-2", "lib-1"]
    assert all(item["kind"] == "library" for item in assets)
    assert [item["asset_id"] for item in store.list_library_assets_for_owner("user-b")] == ["lib-b"]

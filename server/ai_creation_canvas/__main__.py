"""Explicit Python-only release entry point."""

from __future__ import annotations

import argparse
from pathlib import Path
import stat
import sys
import threading
from typing import Callable
from urllib.parse import urlsplit
import webbrowser

import uvicorn

from ai_creation_canvas.app import create_app
from ai_creation_canvas.auth.local import LocalAuthService
from ai_creation_canvas.config import Settings, is_within_production_repository, load_comfyui_service_declarations
from ai_creation_canvas.storage.sqlite import CanvasStore
from ai_creation_canvas.auth.local import BootstrapResult


_MIB = 1024 * 1024
_LOCAL_COMFYUI_HOSTS = frozenset({"127.0.0.1", "::1"})


def _upload_mib(value: str) -> int:
    try:
        amount = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("upload limit must be an integer MiB value") from error
    if not 1 <= amount <= 2048:
        raise argparse.ArgumentTypeError("upload limit must be between 1 and 2048 MiB")
    return amount * _MIB


def _quota_mib(value: str) -> int:
    try:
        amount = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("asset quota must be an integer MiB value") from error
    if not 1 <= amount <= 1024 * 1024:
        raise argparse.ArgumentTypeError("asset quota must be between 1 and 1048576 MiB")
    return amount * _MIB


def _upload_concurrency(value: str) -> int:
    try:
        amount = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("upload concurrency must be an integer") from error
    if not 1 <= amount <= 32:
        raise argparse.ArgumentTypeError("upload concurrency must be between 1 and 32")
    return amount


def _add_upload_limit_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-image-upload-mib", dest="max_image_upload_bytes", type=_upload_mib, default=10 * _MIB, metavar="MIB", help="maximum owned image upload size (1-2048 MiB; default: 10)")
    parser.add_argument("--max-video-upload-mib", dest="max_video_upload_bytes", type=_upload_mib, default=64 * _MIB, metavar="MIB", help="maximum owned video upload size (1-2048 MiB; default: 64)")
    parser.add_argument("--max-audio-upload-mib", dest="max_audio_upload_bytes", type=_upload_mib, default=32 * _MIB, metavar="MIB", help="maximum owned audio upload size (1-2048 MiB; default: 32)")
    parser.add_argument("--upload-concurrency", type=_upload_concurrency, default=4, help="maximum concurrent multipart uploads (1-32; default: 4)")
    parser.add_argument("--user-asset-quota-mib", dest="user_asset_quota_bytes", type=_quota_mib, default=2048 * _MIB, metavar="MIB", help="per-user local asset quota (default: 2048 MiB)")
    parser.add_argument("--total-asset-quota-mib", dest="total_asset_quota_bytes", type=_quota_mib, default=10240 * _MIB, metavar="MIB", help="total local asset quota (default: 10240 MiB)")


def initialize_local_accounts(data_dir: Path, *, initial_model_ids: tuple[str, ...] = (), output: Callable[[str], None] = print) -> bool:
    store = CanvasStore(data_dir)
    result = LocalAuthService(store, session_ttl_seconds=12 * 60 * 60).bootstrap_accounts(initial_model_ids)
    if not result.created:
        output("Local accounts are already initialized; no passwords were displayed.")
        return False
    output("Local accounts created. One-time credentials:")
    output(f"{result.admin_username}: {result.admin_password}")
    output(f"{result.user_username}: {result.user_password}")
    return True


def reset_local_password(data_dir: Path, username: str, *, output: Callable[[str], None] = print) -> str:
    password = LocalAuthService(CanvasStore(data_dir), session_ttl_seconds=12 * 60 * 60).reset_password(username)
    output(f"{username.strip().casefold()}: {password}")
    return password


def _local_comfyui_config(data_dir: Path, configured_path: Path | None) -> tuple[Path | None, Path | None]:
    """Validate a local-only ComfyUI declaration without broadening production policy."""
    if configured_path is None:
        return None, None
    root = Path(data_dir).expanduser().resolve(strict=False) / "config"
    candidate = Path(configured_path).expanduser()
    try:
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        declarations = load_comfyui_service_declarations(candidate, root)
    except (OSError, ValueError) as error:
        if isinstance(error, ValueError) and str(error) == "ComfyUI services configuration is invalid":
            raise
        raise ValueError("local ComfyUI services configuration must be a regular non-symlink file within the local data config root") from None
    if any(urlsplit(declaration.base_url).hostname not in _LOCAL_COMFYUI_HOSTS for declaration in declarations):
        raise ValueError("local ComfyUI services must use a numeric loopback host")
    return candidate, root


def _local_data_dir(data_dir: Path) -> Path:
    """Resolve and reject protected paths before any optional local config is read."""
    resolved = Path(data_dir).expanduser().resolve(strict=False)
    if is_within_production_repository(resolved):
        raise ValueError("non-production environment cannot use the production repository")
    return resolved


def create_local_app(*, port: int, data_dir: Path, static_dir: Path, bootstrap_if_empty: bool = False, ark_models_config: Path | None = None, comfyui_services_config: Path | None = None, prompt_skill_model: str | None = None, redis_url: str | None = None, max_image_upload_bytes: int = 10 * _MIB, max_video_upload_bytes: int = 64 * _MIB, max_audio_upload_bytes: int = 32 * _MIB, upload_concurrency: int = 4, user_asset_quota_bytes: int = 2048 * _MIB, total_asset_quota_bytes: int = 10240 * _MIB):
    origin = f"http://127.0.0.1:{port}"
    local_data_dir = _local_data_dir(data_dir)
    comfy_config_path, comfy_config_root = _local_comfyui_config(local_data_dir, comfyui_services_config)
    settings = Settings(
        environment="development",
        port=port,
        data_dir=local_data_dir,
        portal_internal_token="local-identity-unused-secret",
        identity_mode="local",
        allowed_origins=(origin,),
        enable_demo_adapter=True,
        enable_ark_adapter=ark_models_config is not None,
        ark_models_config_path=ark_models_config,
        ark_models_config_root=ark_models_config.parent if ark_models_config is not None else None,
        comfyui_services_config_path=comfy_config_path,
        comfyui_services_config_root=comfy_config_root,
        prompt_skill_model_id=prompt_skill_model,
        redis_url=redis_url,
        max_image_upload_bytes=max_image_upload_bytes,
        max_video_upload_bytes=max_video_upload_bytes,
        max_audio_upload_bytes=max_audio_upload_bytes,
        upload_concurrency=upload_concurrency,
        user_asset_quota_bytes=user_asset_quota_bytes,
        total_asset_quota_bytes=total_asset_quota_bytes,
    )
    app = create_app(settings, static_dir=static_dir)
    accounts: BootstrapResult | None = None
    if bootstrap_if_empty:
        model_ids = ["demo-image-v1"]
        for adapter in app.state.adapter_registry.generation_adapters():
            model_ids.extend(getattr(adapter, "model_ids", ()))
        accounts = app.state.local_auth.bootstrap_accounts(tuple(sorted(set(model_ids))))
    return app, accounts


def _run_init_local(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Initialize standalone local accounts.")
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    initialize_local_accounts(args.data_dir)


def _run_reset_local_password(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Reset one standalone local account password.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--username", required=True)
    args = parser.parse_args(argv)
    reset_local_password(args.data_dir, args.username)


def _print_bootstrap(result: BootstrapResult | None) -> None:
    if result is None:
        return
    if not result.created:
        print("Local accounts are already initialized; no passwords were displayed.")
        return
    print("Local accounts created. One-time credentials:")
    print(f"{result.admin_username}: {result.admin_password}")
    print(f"{result.user_username}: {result.user_password}")


def _run_serve_local(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Run the standalone local AI Creation Canvas.")
    parser.add_argument("--port", type=int, default=8992)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--static-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-if-empty", action="store_true")
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument("--ark-models", type=Path, help="administrator-owned Ark model declarations; requires ARK_API_KEY")
    parser.add_argument("--comfyui-services", type=Path, help="administrator-owned ComfyUI service declarations")
    parser.add_argument("--prompt-skill-model", help="administrator-owned Ark text endpoint used by built-in prompt skills")
    parser.add_argument("--redis-url", help="optional Redis coordination URL; governed production models require Redis")
    _add_upload_limit_arguments(parser)
    args = parser.parse_args(argv)
    app, accounts = create_local_app(port=args.port, data_dir=args.data_dir, static_dir=args.static_dir, bootstrap_if_empty=args.bootstrap_if_empty, ark_models_config=args.ark_models, comfyui_services_config=args.comfyui_services, prompt_skill_model=args.prompt_skill_model, redis_url=args.redis_url, max_image_upload_bytes=args.max_image_upload_bytes, max_video_upload_bytes=args.max_video_upload_bytes, max_audio_upload_bytes=args.max_audio_upload_bytes, upload_concurrency=args.upload_concurrency, user_asset_quota_bytes=args.user_asset_quota_bytes, total_asset_quota_bytes=args.total_asset_quota_bytes)
    _print_bootstrap(accounts)
    if args.open_browser:
        url = f"http://127.0.0.1:{args.port}/login"
        async def open_after_startup() -> None:
            threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
        app.router.on_startup.append(open_after_startup)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AI Creation Canvas from a staged release.")
    parser.add_argument("--environment", choices=("test", "development", "production"), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--portal-internal-token", required=True)
    parser.add_argument("--portal-base-url", required=True)
    parser.add_argument("--services-config", type=Path, required=True)
    parser.add_argument("--credential-pools", type=Path, help="administrator-owned grouped provider credential pools")
    parser.add_argument("--credential-pools-root", type=Path, help="trusted administrator-owned root for credential pools")
    parser.add_argument("--static-dir", type=Path, default=Path(__file__).parents[2] / "web" / "dist")
    parser.add_argument("--allow-loopback-http", action="store_true")
    parser.add_argument("--check-config", action="store_true", help="validate the declaration file without serving HTTP")
    parser.add_argument("--prompt-skill-model", help="administrator-owned Ark text endpoint used by built-in prompt skills")
    parser.add_argument("--redis-url", help="Redis coordination URL for governed production models")
    _add_upload_limit_arguments(parser)
    return parser.parse_args()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "init-local":
        _run_init_local(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "reset-local-password":
        _run_reset_local_password(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "serve-local":
        _run_serve_local(sys.argv[2:])
        return
    args = _arguments()
    settings = Settings(
        environment=args.environment,
        port=args.port,
        data_dir=args.data_dir,
        portal_internal_token=args.portal_internal_token,
        portal_base_url=args.portal_base_url,
        services_config_path=args.services_config,
        services_config_root=args.services_config.parent,
        credential_pools_path=args.credential_pools,
        credential_pools_root=args.credential_pools_root,
        portal_allow_loopback_http=args.allow_loopback_http,
        max_image_upload_bytes=args.max_image_upload_bytes,
        max_video_upload_bytes=args.max_video_upload_bytes,
        max_audio_upload_bytes=args.max_audio_upload_bytes,
        upload_concurrency=args.upload_concurrency,
        user_asset_quota_bytes=args.user_asset_quota_bytes,
        total_asset_quota_bytes=args.total_asset_quota_bytes,
        prompt_skill_model_id=args.prompt_skill_model,
        redis_url=args.redis_url,
    )
    app = create_app(settings, static_dir=args.static_dir)
    if args.check_config:
        print(" ".join(adapter.service_id for adapter in app.state.adapter_registry.generation_adapters()))
        return
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

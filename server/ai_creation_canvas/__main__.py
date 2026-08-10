"""Explicit Python-only release entry point."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import threading
from typing import Callable
import webbrowser

import uvicorn

from ai_creation_canvas.app import create_app
from ai_creation_canvas.auth.local import LocalAuthService
from ai_creation_canvas.config import Settings
from ai_creation_canvas.storage.sqlite import CanvasStore
from ai_creation_canvas.auth.local import BootstrapResult


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


def create_local_app(*, port: int, data_dir: Path, static_dir: Path, bootstrap_if_empty: bool = False):
    origin = f"http://127.0.0.1:{port}"
    settings = Settings(
        environment="development",
        port=port,
        data_dir=data_dir,
        portal_internal_token="local-identity-unused-secret",
        identity_mode="local",
        allowed_origins=(origin,),
        enable_demo_adapter=True,
    )
    app = create_app(settings, static_dir=static_dir)
    accounts: BootstrapResult | None = None
    if bootstrap_if_empty:
        accounts = app.state.local_auth.bootstrap_accounts(("demo-image-v1",))
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
    args = parser.parse_args(argv)
    app, accounts = create_local_app(port=args.port, data_dir=args.data_dir, static_dir=args.static_dir, bootstrap_if_empty=args.bootstrap_if_empty)
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
    parser.add_argument("--static-dir", type=Path, default=Path(__file__).parents[2] / "web" / "dist")
    parser.add_argument("--allow-loopback-http", action="store_true")
    parser.add_argument("--check-config", action="store_true", help="validate the declaration file without serving HTTP")
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
        portal_allow_loopback_http=args.allow_loopback_http,
    )
    app = create_app(settings, static_dir=args.static_dir)
    if args.check_config:
        print(" ".join(adapter.service_id for adapter in app.state.adapter_registry.generation_adapters()))
        return
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

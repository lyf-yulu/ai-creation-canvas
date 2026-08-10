"""Explicit Python-only release entry point."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Callable

import uvicorn

from ai_creation_canvas.app import create_app
from ai_creation_canvas.auth.local import LocalAuthService
from ai_creation_canvas.config import Settings
from ai_creation_canvas.storage.sqlite import CanvasStore


def initialize_local_accounts(data_dir: Path, *, output: Callable[[str], None] = print) -> bool:
    store = CanvasStore(data_dir)
    result = LocalAuthService(store, session_ttl_seconds=12 * 60 * 60).bootstrap_accounts()
    if not result.created:
        output("Local accounts are already initialized; no passwords were displayed.")
        return False
    output("Local accounts created. One-time credentials:")
    output(f"{result.admin_username}: {result.admin_password}")
    output(f"{result.user_username}: {result.user_password}")
    return True


def _run_init_local(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Initialize standalone local accounts.")
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    initialize_local_accounts(args.data_dir)


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

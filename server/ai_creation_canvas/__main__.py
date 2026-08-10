"""Explicit Python-only release entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings


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

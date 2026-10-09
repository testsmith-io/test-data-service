"""Command-line entry point: ``tds serve``."""

from __future__ import annotations

import argparse
import os

from .config import Config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tds", description="Test Data Service")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the API + web server.")
    serve.add_argument("--host", default=None, help="Bind host (overrides config).")
    serve.add_argument("--port", type=int, default=None, help="Bind port (overrides config).")
    serve.add_argument("--data-dir", default=None, help="Override TDS_DATA_DIR.")
    serve.add_argument("--auth", action="store_true", help="Enable role-based access.")
    serve.add_argument(
        "--admin-key", default=None, help="Bootstrap admin token (sets TDS_ADMIN_KEY)."
    )
    serve.add_argument("--reload", action="store_true", help="Auto-reload (dev).")

    args = parser.parse_args(argv)

    if args.command == "serve":
        if args.data_dir:
            os.environ["TDS_DATA_DIR"] = args.data_dir
        if args.auth:
            os.environ["TDS_AUTH_ENABLED"] = "1"
        if args.admin_key:
            os.environ["TDS_ADMIN_KEY"] = args.admin_key

        import uvicorn

        # Loads tds.yml/tds.yaml from the current directory, then env overrides.
        config = Config.load()
        host = args.host or config.host
        port = args.port or config.port
        url = f"http://{host}:{port}"
        lines = [f"Test Data Service — data dir: {config.data_dir}  ({url})"]
        if config.auth_enabled:
            lines.append(f"  auth: ON — sign in at {url} (default admin: 'admin' / 'admin')")
        else:
            lines.append("  auth: OFF — no sign-in required.")
            lines.append("  Enable it with auth: true in tds.yml (or --auth) to force login.")
        print("\n".join(lines), flush=True)
        # Use the app factory so the app is built *after* the env vars above are
        # set (importing the package otherwise builds a cached app with defaults).
        uvicorn.run(
            "testdataservice.app:create_app",
            factory=True,
            host=host,
            port=port,
            reload=args.reload,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

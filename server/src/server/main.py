from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import re

import uvicorn

from .app import create_app
from .db import Database
from .environment import environment_value
from .settings import Settings

BARE_HOSTNAME = re.compile(
    r"(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*",
    re.IGNORECASE,
)


def domain_error(domain: str | None) -> str | None:
    if not domain:
        return (
            "BUZZ_DOMAIN is not set. Set it to the hostname that serves the dashboard, "
            "such as buzz.example.com, or run with --dev for local development."
        )
    if not BARE_HOSTNAME.fullmatch(domain):
        return (
            f"BUZZ_DOMAIN={domain!r} is not a bare hostname. Use a value such as "
            "buzz.example.com, without a scheme, port, path, or trailing dot."
        )
    return None


def access_control_warning(
    allow_registration: bool, allowed_users: frozenset[str] | None, user_count: int
) -> str | None:
    if allow_registration or allowed_users or user_count:
        return None
    return (
        "WARNING: BUZZ_ALLOW_REGISTRATION is false, BUZZ_ALLOWED_GITHUB_USERS is empty, "
        "and no users exist. Nobody can log in to this server."
    )


def configure_logging() -> None:
    """Uvicorn configures only its own loggers. Without this, Buzz's INFO lines,
    such as a certificate issuance, would never reach the container log."""
    logger = logging.getLogger("server")
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s: %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def main() -> None:
    parser = argparse.ArgumentParser(description="Static site hosting server")
    parser.add_argument("--port", type=int, default=environment_value("BUZZ_PORT"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--domain", help="Domain for hosted sites (env: BUZZ_DOMAIN)")
    parser.add_argument("--dev", action="store_true", help="Bypass authentication")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on changes")
    args = parser.parse_args()

    settings = Settings.from_environment()
    overrides: dict[str, object] = {}
    if args.domain:
        overrides["domain"] = args.domain
    if args.dev:
        overrides["dev_mode"] = True
    if overrides:
        settings = dataclasses.replace(settings, **overrides)
    if not settings.dev_mode:
        error = domain_error(settings.domain)
        if error:
            raise SystemExit(f"ERROR: {error}")

    configure_logging()
    settings.sites_dir.mkdir(parents=True, exist_ok=True)
    database = Database(settings.db_path)
    database.init()
    with database.connect() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if not settings.dev_mode:
        warning = access_control_warning(
            settings.allow_registration, settings.allowed_github_users, user_count
        )
        if warning:
            print(warning)

    print(f"Server running on http://localhost:{args.port}")
    if settings.domain:
        print(f"Serving sites on *.{settings.domain}")
    else:
        print(f"Serving sites on *.localhost:{args.port}")
    if settings.dev_mode:
        print("DEV MODE: Authentication bypassed")
    elif settings.github_client_id and settings.github_client_secret:
        print("GitHub OAuth enabled")
    else:
        print("ERROR: GitHub OAuth not configured")
        print("Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET environment variables")
        print("Or use --dev flag for local development")
        exit(1)

    if args.reload:
        # Reload spawns a fresh interpreter that imports the factory directly, so
        # command-line overrides cannot travel through the app instance. Propagate
        # --domain through the environment; --dev is not honored under --reload.
        if args.domain:
            os.environ["BUZZ_DOMAIN"] = args.domain
        uvicorn.run(
            "server.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=True,
        )
    else:
        uvicorn.run(
            create_app(settings, database),
            host=args.host,
            port=args.port,
        )

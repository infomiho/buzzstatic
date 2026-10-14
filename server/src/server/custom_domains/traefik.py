"""Buzz's Traefik integration through the file provider.

Buzz owns the `buzz-routes.yaml` file in a directory Traefik watches. Every
published document carries a revision router whose response header names the
document's revision. Probing that router through the proxy proves which
revision Traefik serves, so acknowledgement needs no Traefik API access.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import logging
import re
import socket
import ssl
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from ..traefik_file_provider import DynamicConfigError, TraefikDynamicFile

REVISION_ROUTER = "buzz-config-revision"
REVISION_HOST = "buzz-config-revision.invalid"
REVISION_HEADER = "X-Buzz-Config-Revision"
REVISION_MAX_AGE = timedelta(seconds=30)
PROBE_TIMEOUT_SECONDS = 3
ROUTER_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,127}")
logger = logging.getLogger(__name__)


def revision_max_age(reconcile_seconds: float) -> timedelta:
    """Acknowledgements refresh once per reconcile run, which jitters by 20%."""
    return max(REVISION_MAX_AGE, timedelta(seconds=3 * reconcile_seconds))


def dynamic_config(routers: Mapping[str, dict]) -> dict[str, Any]:
    return {"http": {"routers": dict(routers)}} if routers else {}


def config_revision(document: Mapping[str, Any]) -> str:
    canonical = json.dumps(document, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def with_revision_router(
    document: Mapping[str, Any],
    revision: str,
    https_entrypoint: str,
    service: str,
) -> dict[str, Any]:
    """The revision router uses the same entrypoint and service as custom-domain
    routers, so a probe answer also proves both exist in Traefik."""
    http_section = dict(document.get("http", {}))
    http_section["routers"] = {
        **http_section.get("routers", {}),
        REVISION_ROUTER: {
            "entryPoints": [https_entrypoint],
            "middlewares": [REVISION_ROUTER],
            "rule": f"Host(`{REVISION_HOST}`)",
            "service": service,
            "tls": {},
        },
    }
    http_section["middlewares"] = {
        REVISION_ROUTER: {
            "headers": {"customResponseHeaders": {REVISION_HEADER: revision}}
        }
    }
    return {**document, "http": http_section}


def probe_config_revision(origin_host: str) -> str | None:
    """Raises OSError when the proxy cannot be reached. Certificate checks are
    off because the revision router deliberately uses Traefik's default cert."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection(
            (origin_host, 443), timeout=PROBE_TIMEOUT_SECONDS
        ) as connection:
            with context.wrap_socket(connection, server_hostname=REVISION_HOST) as tls:
                tls.sendall(
                    (
                        "HEAD / HTTP/1.1\r\n"
                        f"Host: {REVISION_HOST}\r\n"
                        "Connection: close\r\n\r\n"
                    ).encode("ascii")
                )
                response = http.client.HTTPResponse(tls, method="HEAD")
                response.begin()
                return response.getheader(REVISION_HEADER)
    except http.client.HTTPException as exc:
        raise OSError("Traefik returned an invalid response") from exc


class TraefikFileControl:
    def __init__(
        self,
        config_file: TraefikDynamicFile,
        origin_host: str,
        https_entrypoint: str,
        service: str,
        probe: Callable[[str], str | None] | None = None,
        max_age: timedelta = REVISION_MAX_AGE,
    ):
        self._file = config_file
        self._max_age = max_age
        self._origin_host = origin_host
        self._https_entrypoint = https_entrypoint
        self._service = service
        self._probe = probe or probe_config_revision
        self._lock = threading.Lock()
        self._published: tuple[str, frozenset[str]] | None = None
        self._observed: str | None = None
        self._loaded_at: datetime | None = None
        self._write_failed = False

    def publish(self, routers: Mapping[str, dict]) -> None:
        for name in routers:
            if name == REVISION_ROUTER or not ROUTER_NAME.fullmatch(name):
                raise DynamicConfigError(f"Invalid Traefik router name {name!r}")
        document = dynamic_config(routers)
        revision = config_revision(document)
        try:
            changed = self._file.write(
                with_revision_router(
                    document, revision, self._https_entrypoint, self._service
                )
            )
        except (OSError, DynamicConfigError):
            with self._lock:
                self._write_failed = True
            raise
        with self._lock:
            self._published = (revision, frozenset(routers))
            self._write_failed = False
        if changed:
            logger.info(
                "Traefik dynamic configuration revision %s written with %d routers",
                revision,
                len(routers),
            )

    def refresh(self) -> None:
        try:
            observed = self._probe(self._origin_host)
        except OSError:
            observed = None
        with self._lock:
            self._observed = observed
            if self._published and observed == self._published[0]:
                self._loaded_at = datetime.now(timezone.utc)

    def loaded_routers(self) -> frozenset[str] | None:
        """Routers in the revision Traefik serves, or None until Traefik serves
        exactly the revision Buzz last wrote."""
        with self._lock:
            if self._published and self._observed == self._published[0]:
                return self._published[1]
            return None

    def is_ready(self) -> bool:
        with self._lock:
            loaded_at = self._loaded_at
            write_failed = self._write_failed
        return bool(
            not write_failed
            and loaded_at
            and datetime.now(timezone.utc) - loaded_at <= self._max_age
        )

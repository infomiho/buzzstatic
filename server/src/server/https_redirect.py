"""Sends plain-HTTP requests for BUZZ_DOMAIN and its site subdomains to HTTPS.

Buzz publishes the redirect router through Traefik's file provider instead of
relying on the proxy's static configuration, which on Coolify belongs to
Coolify. A failed write is logged and never stops Buzz from serving.
"""
from __future__ import annotations

import logging
from typing import Any

from .settings import Settings
from .traefik_file_provider import DynamicConfigError, TraefikDynamicFile

DYNAMIC_FILE_NAME = "buzz-http"
ROUTER = "buzz-http"
MIDDLEWARE = "buzz-redirect-to-https"
# The redirect answers before any service is called, so the router needs none of its own.
NOOP_SERVICE = "noop@internal"
logger = logging.getLogger(__name__)


def canonical_hosts_rule(domain: str) -> str:
    escaped_domain = domain.replace(".", r"\.")
    return f"Host(`{domain}`) || HostRegexp(`.+\\.{escaped_domain}`)"


def https_redirect_config(domain: str, http_entrypoint: str) -> dict[str, Any]:
    return {
        "http": {
            "routers": {
                ROUTER: {
                    "entryPoints": [http_entrypoint],
                    "rule": canonical_hosts_rule(domain),
                    "middlewares": [MIDDLEWARE],
                    "service": NOOP_SERVICE,
                }
            },
            "middlewares": {
                MIDDLEWARE: {"redirectScheme": {"scheme": "https", "permanent": True}}
            },
        }
    }


class HttpsRedirect:
    def __init__(self, config_file: TraefikDynamicFile, domain: str, http_entrypoint: str):
        self._file = config_file
        self._domain = domain
        self._http_entrypoint = http_entrypoint

    @classmethod
    def from_settings(cls, settings: Settings) -> "HttpsRedirect | None":
        if settings.dev_mode or not settings.domain or not settings.traefik_dynamic_dir:
            return None
        return cls(
            TraefikDynamicFile(settings.traefik_dynamic_dir, DYNAMIC_FILE_NAME),
            settings.domain,
            settings.traefik_http_entrypoint,
        )

    def publish(self) -> bool:
        """Reports whether the redirect is in place."""
        try:
            changed = self._file.write(
                https_redirect_config(self._domain, self._http_entrypoint)
            )
        except DynamicConfigError:
            logger.exception("Could not publish the HTTP to HTTPS redirect")
            return False
        if changed:
            logger.info(
                "HTTP to HTTPS redirect published for %s and *.%s",
                self._domain,
                self._domain,
            )
        return True

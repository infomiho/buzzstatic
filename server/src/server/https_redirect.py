"""Sends plain-HTTP requests for BUZZ_DOMAIN and its site subdomains to HTTPS.

Buzz publishes the redirect router through Traefik's file provider instead of
relying on the proxy's static configuration, which on Coolify belongs to
Coolify. A background loop republishes the router every few minutes, so a
deleted file comes back, and asks the proxy over plain HTTP whether it really
redirects. A failure is logged and reported, and never stops Buzz from serving.
"""
from __future__ import annotations

import asyncio
import http.client
import logging
from collections.abc import Callable
from typing import Any

from .settings import Settings
from .traefik_file_provider import DynamicConfigError, TraefikDynamicFile

DYNAMIC_FILE_NAME = "buzz-http"
ROUTER = "buzz-http"
MIDDLEWARE = "buzz-redirect-to-https"
# The redirect answers before any service is called, so the router needs none of its own.
NOOP_SERVICE = "noop@internal"
PROXY_HTTP_PORT = 80
CHECK_INTERVAL_SECONDS = 5 * 60
PUBLISH_SETTLE_SECONDS = 5
PROBE_TIMEOUT_SECONDS = 5
PUBLISH_FAILED = "publish_failed"
PROXY_UNREACHABLE = "proxy_unreachable"
NOT_REDIRECTING = "not_redirecting"
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


def probe_https_redirect(proxy_host: str, domain: str) -> str | None:
    """Returns an error code unless the proxy answers plain HTTP for the domain
    with a redirect to HTTPS."""
    connection = http.client.HTTPConnection(
        proxy_host, PROXY_HTTP_PORT, timeout=PROBE_TIMEOUT_SECONDS
    )
    try:
        connection.request("HEAD", "/", headers={"Host": domain})
        response = connection.getresponse()
    except (OSError, http.client.HTTPException):
        return PROXY_UNREACHABLE
    finally:
        connection.close()
    location = response.getheader("Location") or ""
    redirects_to_https = 300 <= response.status < 400 and location.startswith("https://")
    return None if redirects_to_https else NOT_REDIRECTING


class HttpsRedirect:
    def __init__(
        self,
        config_file: TraefikDynamicFile,
        domain: str,
        http_entrypoint: str,
        proxy_host: str | None = None,
        probe: Callable[[str, str], str | None] = probe_https_redirect,
        interval_seconds: float = CHECK_INTERVAL_SECONDS,
        settle_seconds: float = PUBLISH_SETTLE_SECONDS,
    ):
        self._file = config_file
        self._domain = domain
        self._http_entrypoint = http_entrypoint
        self._proxy_host = proxy_host
        self._probe = probe
        self._interval_seconds = interval_seconds
        self._settle_seconds = settle_seconds
        self._published = False
        self._stopping = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.error_code: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "HttpsRedirect | None":
        if settings.dev_mode or not settings.domain or not settings.traefik_dynamic_dir:
            return None
        return cls(
            TraefikDynamicFile(settings.traefik_dynamic_dir, DYNAMIC_FILE_NAME),
            settings.domain,
            settings.traefik_http_entrypoint,
            proxy_host=settings.custom_domain_origin_host,
        )

    def start(self) -> None:
        self._published = self.publish()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if not self._task:
            return
        self._stopping.set()
        await self._task
        self._task = None

    def check(self) -> str | None:
        if not self._published:
            return PUBLISH_FAILED
        if not self._proxy_host:
            return None
        return self._probe(self._proxy_host, self._domain)

    async def _run(self) -> None:
        """The proxy needs a moment to load a published file before it is probed."""
        while not await self._stopped_within(self._settle_seconds):
            await self._record_check()
            if await self._stopped_within(self._interval_seconds):
                break
            self._published = await asyncio.to_thread(self.publish)

    async def _record_check(self) -> None:
        previous = self.error_code
        try:
            self.error_code = await asyncio.to_thread(self.check)
        except Exception:
            logger.exception("HTTP to HTTPS redirect check failed")
            return
        if self.error_code and self.error_code != previous:
            logger.warning("HTTP to HTTPS redirect check: %s", self.error_code)

    async def _stopped_within(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        except TimeoutError:
            return False
        return True

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

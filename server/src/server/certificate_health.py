"""Checks the wildcard certificate the proxy serves for site subdomains, so a
failed renewal shows up weeks before every site breaks. The check only reports;
it never changes how sites are served."""
from __future__ import annotations

import asyncio
import logging
import secrets
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

RENEWAL_MARGIN = timedelta(days=21)
CHECK_INTERVAL_SECONDS = 5 * 60
FIRST_CHECK_DELAY_SECONDS = 10
PUBLISH_SETTLE_SECONDS = 5
PROBE_TIMEOUT_SECONDS = 5
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CertificateHealth:
    ok: bool
    reason: str | None
    detail: str | None
    expires_at: datetime | None
    checked_at: datetime

    @property
    def severity(self) -> str:
        """Only an invalid certificate breaks sites now. The rest are early warnings."""
        return "danger" if self.reason == "certificate_invalid" else "attention"

    def message(self, domain: str) -> str | None:
        if self.reason == "proxy_unreachable":
            return f"Buzz cannot reach the proxy to check the certificate for *.{domain}."
        if self.reason == "certificate_invalid":
            return f"The proxy does not serve a valid certificate for *.{domain}: {self.detail}."
        if self.reason == "certificate_expiring":
            expires = self.expires_at.date().isoformat() if self.expires_at else "soon"
            return (
                f"The certificate for *.{domain} expires on {expires}. "
                "Its renewal may be failing."
            )
        return None


def inspect_wildcard_certificate(
    proxy_host: str,
    domain: str,
    now: datetime | None = None,
) -> CertificateHealth:
    """Connects with SNI for a random subdomain, so the proxy can only answer
    with a certificate that covers every site."""
    now = now or datetime.now(timezone.utc)
    server_name = f"buzz-health-{secrets.token_hex(4)}.{domain}"
    try:
        with socket.create_connection(
            (proxy_host, 443), timeout=PROBE_TIMEOUT_SECONDS
        ) as connection:
            with ssl.create_default_context().wrap_socket(
                connection, server_hostname=server_name
            ) as tls:
                certificate = tls.getpeercert()
    except ssl.SSLCertVerificationError as exc:
        return CertificateHealth(False, "certificate_invalid", exc.verify_message, None, now)
    except (OSError, ssl.SSLError) as exc:
        return CertificateHealth(False, "proxy_unreachable", str(exc), None, now)
    expires_at = datetime.fromtimestamp(
        ssl.cert_time_to_seconds(certificate["notAfter"]), timezone.utc
    )
    if expires_at - now < RENEWAL_MARGIN:
        return CertificateHealth(False, "certificate_expiring", None, expires_at, now)
    return CertificateHealth(True, None, None, expires_at, now)


class WildcardCertificateMonitor:
    def __init__(
        self,
        proxy_host: str,
        domain: str,
        inspect=inspect_wildcard_certificate,
        interval_seconds: float = CHECK_INTERVAL_SECONDS,
        first_check_delay_seconds: float = FIRST_CHECK_DELAY_SECONDS,
    ):
        self._proxy_host = proxy_host
        self._domain = domain
        self._inspect = inspect
        self._interval_seconds = interval_seconds
        self._first_check_delay_seconds = first_check_delay_seconds
        self._wake = asyncio.Event()
        self._stopping = False
        self._task: asyncio.Task | None = None
        self.health: CertificateHealth | None = None

    @property
    def domain(self) -> str:
        return self._domain

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def check_soon(self) -> None:
        """Called after Buzz publishes a certificate, so /health does not keep
        reporting the certificate it replaced until the next interval."""
        self._wake.set()

    async def stop(self) -> None:
        if not self._task:
            return
        self._stopping = True
        self._wake.set()
        await self._task
        self._task = None

    async def _run(self) -> None:
        delay = self._first_check_delay_seconds
        while not await self._stopped_within(delay):
            try:
                health = await asyncio.to_thread(
                    self._inspect, self._proxy_host, self._domain
                )
            except Exception:
                logger.exception("Wildcard certificate check failed")
            else:
                if not health.ok:
                    logger.warning(
                        "Wildcard certificate check: %s", health.message(self._domain)
                    )
                self.health = health
            delay = self._interval_seconds

    async def _stopped_within(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except TimeoutError:
            return False
        if self._stopping:
            return True
        self._wake.clear()
        return await self._stopped_within(PUBLISH_SETTLE_SECONDS)

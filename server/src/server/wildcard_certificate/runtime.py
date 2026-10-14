"""Background loop that keeps the wildcard certificate current and republishes
it every few minutes, so a deleted or overwritten file comes back before the
proxy serves its default certificate for long. Republishing an unchanged file
is a no-op. A failure is logged and retried with backoff, and never stops Buzz
from serving."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ..settings import Settings
from ..traefik_file_provider import TraefikDynamicFile
from ..utils import write_file_atomically
from .acme import AcmeClient, DnsChallengeSolver
from .cloudflare_dns import CloudflareDns
from .manager import CertificateStatus, Issuer, WildcardCertificateManager

DYNAMIC_FILE_NAME = "buzz-certificate"
CHECK_INTERVAL = timedelta(minutes=5)
RETRY_DELAYS = (
    timedelta(minutes=2),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(minutes=30),
)
NOT_CONFIGURED = "not_configured"
CHECK_FAILED = "check_failed"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WildcardCertificateConfig:
    domain: str
    state_dir: Path
    dynamic_dir: Path
    acme_directory_url: str
    cloudflare_api_token: str

    @classmethod
    def from_settings(cls, settings: Settings) -> "WildcardCertificateConfig | None":
        if not (
            settings.domain
            and not settings.dev_mode
            and settings.traefik_dynamic_dir
            and settings.cloudflare_dns_api_token
        ):
            return None
        return cls(
            domain=settings.domain,
            state_dir=settings.data_dir / "tls",
            dynamic_dir=settings.traefik_dynamic_dir,
            acme_directory_url=settings.acme_directory_url,
            cloudflare_api_token=settings.cloudflare_dns_api_token,
        )


def missing_token_status(settings: Settings) -> CertificateStatus | None:
    """Buzz publishes through the dynamic directory, so the operator expects it
    to own the certificate. Without a token that cannot happen, which must be
    visible rather than silent."""
    if settings.dev_mode or not settings.domain or not settings.traefik_dynamic_dir:
        return None
    if settings.cloudflare_dns_api_token:
        return None
    return CertificateStatus(
        None,
        False,
        False,
        f"BUZZ_CLOUDFLARE_DNS_API_TOKEN is not set, so Buzz cannot obtain or renew "
        f"the certificate for *.{settings.domain}",
        NOT_CONFIGURED,
    )


def next_check_delay(consecutive_failures: int) -> timedelta:
    if not consecutive_failures:
        return CHECK_INTERVAL
    return RETRY_DELAYS[min(consecutive_failures, len(RETRY_DELAYS)) - 1]


def load_or_create_account_key(path: Path) -> ec.EllipticCurvePrivateKey:
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except FileNotFoundError:
        key = ec.generate_private_key(ec.SECP256R1())
        path.parent.mkdir(parents=True, exist_ok=True)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        write_file_atomically(path, pem.decode())
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError(f"{path} is not an EC private key")
    return key


def acme_issuer(config: WildcardCertificateConfig, dns: DnsChallengeSolver) -> Issuer:
    def issue(domains: list[str], key: ec.EllipticCurvePrivateKey) -> str:
        account_key = load_or_create_account_key(config.state_dir / "acme-account.key")
        client = AcmeClient(config.acme_directory_url, account_key)
        return client.issue(domains, key, dns)

    return issue


def build_manager(
    config: WildcardCertificateConfig, dns: DnsChallengeSolver | None = None
) -> WildcardCertificateManager:
    return WildcardCertificateManager(
        config.domain,
        config.state_dir / "wildcard-certificate.json",
        TraefikDynamicFile(config.dynamic_dir, DYNAMIC_FILE_NAME),
        acme_issuer(config, dns or CloudflareDns(config.cloudflare_api_token)),
        directory_url=config.acme_directory_url,
    )


class WildcardCertificateRuntime:
    def __init__(
        self,
        manager: WildcardCertificateManager | None,
        on_published: Callable[[], None] | None = None,
    ):
        self._manager = manager
        self._on_published = on_published
        self.status: CertificateStatus | None = None
        self._consecutive_failures = 0
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    @classmethod
    def from_settings(
        cls, settings: Settings, on_published: Callable[[], None] | None = None
    ) -> "WildcardCertificateRuntime":
        config = WildcardCertificateConfig.from_settings(settings)
        if config:
            return cls(build_manager(config), on_published)
        runtime = cls(None)
        runtime.status = missing_token_status(settings)
        return runtime

    @property
    def enabled(self) -> bool:
        return self._manager is not None

    @property
    def error(self) -> str | None:
        return self.status.error if self.status else None

    @property
    def error_code(self) -> str | None:
        return self.status.error_code if self.status else None

    def start(self) -> None:
        if self.error_code == NOT_CONFIGURED:
            logger.warning("Wildcard certificate: %s", self.error)
        if self._manager and not self._task:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._stopping.set()
            await self._task
            self._task = None

    async def check_once(self) -> CertificateStatus:
        previous = self.status
        try:
            self.status = await asyncio.to_thread(self._manager.ensure)
        except Exception as exc:
            logger.exception("Wildcard certificate check failed")
            self.status = CertificateStatus(None, False, False, str(exc), CHECK_FAILED)
        self._consecutive_failures = self._consecutive_failures + 1 if self.status.error else 0
        if self.status.error:
            logger.error("Wildcard certificate: %s", self.status.error)
        if self.status.renewed:
            logger.info("Wildcard certificate issued, valid until %s", self.status.not_after)
        newly_published = self.status.published and not (previous and previous.published)
        if self._on_published and (self.status.renewed or newly_published):
            self._on_published()
        return self.status

    async def _run(self) -> None:
        while not self._stopping.is_set():
            await self.check_once()
            delay = next_check_delay(self._consecutive_failures)
            try:
                await asyncio.wait_for(self._stopping.wait(), delay.total_seconds())
            except TimeoutError:
                pass

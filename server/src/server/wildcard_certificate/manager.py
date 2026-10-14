"""Keeps one certificate for BUZZ_DOMAIN and *.BUZZ_DOMAIN current and published.

The data volume holds the source of truth. The Traefik dynamic file is a
projection of it, rewritten whenever it is missing or differs, so a deleted or
stale file heals on the next check. An issued certificate is kept in memory
until it is saved, so a failed save never costs another order.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ..traefik_file_provider import TraefikDynamicFile
from ..utils import write_file_atomically

Issuer = Callable[[list[str], ec.EllipticCurvePrivateKey], str]

ISSUANCE_FAILED = "issuance_failed"
STORAGE_FAILED = "storage_failed"
PUBLISH_FAILED = "publish_failed"
logger = logging.getLogger(__name__)


class CertificateRejected(Exception):
    pass


@dataclass(frozen=True)
class StoredCertificate:
    chain_pem: str
    key_pem: str
    leaf: x509.Certificate
    directory_url: str | None = None

    @property
    def not_after(self) -> datetime:
        return self.leaf.not_valid_after_utc

    def covers(self, domains: list[str]) -> bool:
        try:
            names = self.leaf.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value.get_values_for_type(x509.DNSName)
        except x509.ExtensionNotFound:
            return False
        return {domain.lower() for domain in domains} <= {name.lower() for name in names}

    def renewal_due(self, now: datetime) -> bool:
        lifetime = self.not_after - self.leaf.not_valid_before_utc
        return now >= self.not_after - lifetime / 3

    def issued_by_another_directory(self, directory_url: str | None) -> bool:
        return bool(
            self.directory_url and directory_url and self.directory_url != directory_url
        )

    def state(self) -> dict[str, str]:
        state = {"chain": self.chain_pem, "key": self.key_pem}
        if self.directory_url:
            state["directory_url"] = self.directory_url
        return state

    def traefik_document(self) -> dict:
        return {"tls": {"certificates": [{"certFile": self.chain_pem, "keyFile": self.key_pem}]}}


@dataclass(frozen=True)
class CertificateStatus:
    not_after: datetime | None
    renewed: bool
    published: bool
    error: str | None
    error_code: str | None = None


def load_certificate(chain_pem: str, key_pem: str) -> StoredCertificate:
    try:
        leaf = x509.load_pem_x509_certificates(chain_pem.encode())[0]
        key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    except (ValueError, TypeError, IndexError) as exc:
        raise CertificateRejected(f"Unreadable certificate or key: {exc}") from exc
    if leaf.public_key() != key.public_key():
        raise CertificateRejected("Certificate does not match its private key")
    return StoredCertificate(chain_pem, key_pem, leaf)


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Serializes checks across containers sharing the data volume, so two
    Buzz containers never order the same certificate at once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def describe(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


class WildcardCertificateManager:
    def __init__(
        self,
        domain: str,
        state_path: Path,
        dynamic_file: TraefikDynamicFile,
        issue: Issuer,
        directory_url: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        domain = domain.lower()
        self.domains = [domain, f"*.{domain}"]
        self._state_path = state_path
        self._lock_path = state_path.with_suffix(".lock")
        self._dynamic_file = dynamic_file
        self._issue = issue
        self._directory_url = directory_url
        self._clock = clock
        self._unsaved: StoredCertificate | None = None

    def ensure(self) -> CertificateStatus:
        with exclusive_lock(self._lock_path):
            return self._ensure()

    def stored(self) -> StoredCertificate | None:
        try:
            state = json.loads(self._state_path.read_text())
            certificate = load_certificate(state["chain"], state["key"])
            return replace(certificate, directory_url=state.get("directory_url"))
        except (FileNotFoundError, ValueError, KeyError, TypeError, CertificateRejected):
            return None

    def _ensure(self) -> CertificateStatus:
        now = self._clock()
        current = self._unsaved or self.stored()
        renewed = False
        error_code = error = None
        if self._needs_new_certificate(current, now):
            try:
                current = self._renew(now)
                renewed = True
            except Exception as exc:
                error_code, error = ISSUANCE_FAILED, describe(exc)
        if self._unsaved:
            try:
                self._save(self._unsaved)
                self._unsaved = None
            except OSError as exc:
                if not error:
                    error_code = STORAGE_FAILED
                    error = f"Cannot save {self._state_path}: {exc}"
        if current is None:
            return CertificateStatus(None, renewed, False, error, error_code)
        try:
            rewritten = self._dynamic_file.write(current.traefik_document())
        except Exception as exc:
            return CertificateStatus(
                current.not_after,
                renewed,
                False,
                error or str(exc),
                error_code or PUBLISH_FAILED,
            )
        if rewritten and not renewed:
            logger.warning(
                "%s was missing or changed, so Buzz wrote it again", self._dynamic_file.path.name
            )
        return CertificateStatus(current.not_after, renewed, True, error, error_code)

    def _needs_new_certificate(self, current: StoredCertificate | None, now: datetime) -> bool:
        return (
            current is None
            or not current.covers(self.domains)
            or current.renewal_due(now)
            or current.issued_by_another_directory(self._directory_url)
        )

    def _renew(self, now: datetime) -> StoredCertificate:
        key = ec.generate_private_key(ec.SECP256R1())
        chain_pem = self._issue(self.domains, key)
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        issued = load_certificate(chain_pem, key_pem)
        if not issued.covers(self.domains):
            raise CertificateRejected(f"Issued certificate does not cover {self.domains}")
        if issued.not_after <= now:
            raise CertificateRejected("Issued certificate is already expired")
        self._unsaved = replace(issued, directory_url=self._directory_url)
        return self._unsaved

    def _save(self, certificate: StoredCertificate) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        write_file_atomically(self._state_path, json.dumps(certificate.state()))

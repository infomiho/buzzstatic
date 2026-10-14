"""DNS-01 orders through certbot's `acme` library with an ES256 account key."""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import josepy as jose
import requests
from acme import challenges, client, errors, messages
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .. import __version__

ORDER_TIMEOUT = timedelta(minutes=3)
REQUEST_TIMEOUT_SECONDS = 30
logger = logging.getLogger(__name__)


class AcmeError(Exception):
    pass


class DnsChallengeSolver(Protocol):
    def present(self, record_name: str, value: str) -> object: ...

    def wait_until_visible(self, record_name: str, values: set[str]) -> None: ...

    def cleanup(self, record: object) -> None: ...


@dataclass(frozen=True)
class DnsChallenge:
    challenge: messages.ChallengeBody
    record_name: str
    value: str


def build_csr(domains: list[str], key: ec.EllipticCurvePrivateKey) -> bytes:
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([]))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(domain) for domain in domains]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM)


def describe(error: Exception) -> str:
    if isinstance(error, errors.ValidationError):
        return "; ".join(
            f"Authorization for {authorization.body.identifier.value} is "
            f"{authorization.body.status.name}: {challenge_error(authorization)}"
            for authorization in error.failed_authzrs
        )
    return " ".join(str(error).split()) or type(error).__name__


def challenge_error(authorization: messages.AuthorizationResource) -> str:
    for challenge in authorization.body.challenges:
        if challenge.error:
            return challenge.error.detail or challenge.error.typ
    return "no detail"


class AcmeClient:
    def __init__(
        self,
        directory_url: str,
        account_key: ec.EllipticCurvePrivateKey,
        session: requests.Session | None = None,
    ):
        if not isinstance(account_key.curve, ec.SECP256R1):
            raise ValueError("ACME account key must use P-256")
        self._directory_url = directory_url
        self._account_key = jose.JWKEC(key=account_key)
        self._network = client.ClientNetwork(
            self._account_key,
            alg=jose.ES256,
            user_agent=f"buzz-server/{__version__}",
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if session:
            self._network.session = session

    def issue(
        self,
        domains: list[str],
        certificate_key: ec.EllipticCurvePrivateKey,
        dns: DnsChallengeSolver,
    ) -> str:
        """Orders a certificate for the domains and returns the PEM chain."""
        try:
            return self._issue(domains, certificate_key, dns)
        except (errors.Error, requests.RequestException) as exc:
            raise AcmeError(describe(exc)) from exc

    def _issue(
        self,
        domains: list[str],
        certificate_key: ec.EllipticCurvePrivateKey,
        dns: DnsChallengeSolver,
    ) -> str:
        acme = self._registered_client()
        order = acme.new_order(build_csr(domains, certificate_key))
        pending = [
            self._dns_challenge(authorization)
            for authorization in order.authorizations
            if authorization.body.status != messages.STATUS_VALID
        ]
        values_by_name: dict[str, set[str]] = defaultdict(set)
        for item in pending:
            values_by_name[item.record_name].add(item.value)
        records = []
        try:
            for item in pending:
                records.append(dns.present(item.record_name, item.value))
            for record_name, values in values_by_name.items():
                dns.wait_until_visible(record_name, values)
            for item in pending:
                acme.answer_challenge(item.challenge, item.challenge.response(self._account_key))
            deadline = datetime.now() + ORDER_TIMEOUT
            order = acme.poll_authorizations(order, deadline)
        finally:
            for record in records:
                try:
                    dns.cleanup(record)
                except Exception:
                    logger.warning("Could not remove ACME challenge record", exc_info=True)
        return acme.finalize_order(order, deadline).fullchain_pem

    def _registered_client(self) -> client.ClientV2:
        directory = client.ClientV2.get_directory(self._directory_url, self._network)
        acme = client.ClientV2(directory, self._network)
        registration = messages.NewRegistration.from_data(terms_of_service_agreed=True)
        try:
            acme.new_account(registration)
        except errors.ConflictError as existing:
            self._network.account = messages.RegistrationResource(
                uri=existing.location, body=messages.Registration()
            )
        return acme

    def _dns_challenge(self, authorization: messages.AuthorizationResource) -> DnsChallenge:
        challenge = next(
            (
                body
                for body in authorization.body.challenges
                if isinstance(body.chall, challenges.DNS01)
            ),
            None,
        )
        if challenge is None:
            raise AcmeError("ACME server offered no dns-01 challenge")
        return DnsChallenge(
            challenge,
            challenge.chall.validation_domain_name(authorization.body.identifier.value),
            challenge.chall.validation(self._account_key),
        )

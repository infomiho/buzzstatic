from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


class FakeCa:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fake CA")])
        self.certificate = (
            x509.CertificateBuilder()
            .subject_name(self.name)
            .issuer_name(self.name)
            .public_key(self.key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(NOW - timedelta(days=365))
            .not_valid_after(NOW + timedelta(days=3650))
            .sign(self.key, hashes.SHA256())
        )

    def issue(
        self,
        public_key,
        domains: list[str],
        not_before: datetime = NOW,
        lifetime: timedelta = timedelta(days=90),
    ) -> str:
        leaf = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([]))
            .issuer_name(self.name)
            .public_key(public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before)
            .not_valid_after(not_before + lifetime)
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(d) for d in domains]),
                critical=False,
            )
            .sign(self.key, hashes.SHA256())
        )
        return "".join(
            cert.public_bytes(serialization.Encoding.PEM).decode()
            for cert in (leaf, self.certificate)
        )

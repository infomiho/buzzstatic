import asyncio
import re
import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from server.auth_service import DEV_SESSION_ID
from server.certificate_health import (
    CertificateHealth,
    WildcardCertificateMonitor,
    inspect_wildcard_certificate,
)
from server.cookies import session_cookie_name

NOW = datetime.now(timezone.utc)


def name(common_name):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def issue(tmp_path, sans, valid_days):
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(name("Test CA"))
        .issuer_name(name("Test CA"))
        .public_key(ca_key.public_key())
        .serial_number(1)
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(name(sans[0]))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(2)
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=valid_days))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(san) for san in sans]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    (tmp_path / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "leaf.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "leaf.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    """Serves one TLS handshake with the issued certificate and trusts its CA."""

    def start(sans, valid_days=90, trusted=True):
        issue(tmp_path, sans, valid_days)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tmp_path / "leaf.pem", tmp_path / "leaf.key")
        listener = socket.create_server(("127.0.0.1", 0))
        port = listener.getsockname()[1]

        def serve():
            connection, _ = listener.accept()
            try:
                with context.wrap_socket(connection, server_side=True):
                    pass
            except (ssl.SSLError, OSError):
                pass
            listener.close()

        threading.Thread(target=serve, daemon=True).start()
        real_create_connection = socket.create_connection
        monkeypatch.setattr(
            "server.certificate_health.socket.create_connection",
            lambda address, timeout: real_create_connection(("127.0.0.1", port), timeout),
        )
        if trusted:
            real_default_context = ssl.create_default_context
            monkeypatch.setattr(
                "server.certificate_health.ssl.create_default_context",
                lambda: real_default_context(cafile=tmp_path / "ca.pem"),
            )

    return start


def test_valid_wildcard_certificate_is_healthy(proxy):
    proxy(["buzz.example.com", "*.buzz.example.com"])

    health = inspect_wildcard_certificate("coolify-proxy", "buzz.example.com", now=NOW)

    assert health.ok is True
    assert health.reason is None
    assert health.expires_at > NOW + timedelta(days=89)


def test_certificate_without_wildcard_is_invalid(proxy):
    proxy(["buzz.example.com"])

    health = inspect_wildcard_certificate("coolify-proxy", "buzz.example.com", now=NOW)

    assert health.ok is False
    assert health.reason == "certificate_invalid"
    assert "Hostname mismatch" in health.detail


def test_untrusted_default_certificate_is_invalid(proxy):
    proxy(["TRAEFIK DEFAULT CERT"], trusted=False)

    health = inspect_wildcard_certificate("coolify-proxy", "buzz.example.com", now=NOW)

    assert health.reason == "certificate_invalid"


def test_certificate_close_to_expiry_is_expiring(proxy):
    proxy(["*.buzz.example.com"], valid_days=20)

    health = inspect_wildcard_certificate("coolify-proxy", "buzz.example.com", now=NOW)

    assert health.reason == "certificate_expiring"
    assert "expires on" in health.message("buzz.example.com")


def test_unreachable_proxy_is_reported(monkeypatch):
    def refuse(address, timeout):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr("server.certificate_health.socket.create_connection", refuse)

    health = inspect_wildcard_certificate("coolify-proxy", "buzz.example.com", now=NOW)

    assert health.reason == "proxy_unreachable"


def test_monitor_checks_in_background_and_stops_promptly():
    checked = []

    def inspect(proxy_host, domain):
        checked.append((proxy_host, domain))
        return CertificateHealth(True, None, None, NOW + timedelta(days=60), NOW)

    async def scenario():
        monitor = WildcardCertificateMonitor(
            "traefik", "buzz.example.com", inspect, interval_seconds=60, first_check_delay_seconds=0
        )
        monitor.start()
        for _ in range(100):
            if monitor.health:
                break
            await asyncio.sleep(0.01)
        await asyncio.wait_for(monitor.stop(), timeout=1)
        return monitor.health

    assert asyncio.run(scenario()).ok is True
    assert checked == [("traefik", "buzz.example.com")]


def test_monitor_rechecks_soon_after_a_certificate_is_published(monkeypatch):
    monkeypatch.setattr("server.certificate_health.PUBLISH_SETTLE_SECONDS", 0)
    checked = []

    def inspect(proxy_host, domain):
        checked.append(domain)
        return CertificateHealth(True, None, None, NOW + timedelta(days=60), NOW)

    async def scenario():
        monitor = WildcardCertificateMonitor(
            "traefik", "buzz.example.com", inspect, interval_seconds=60, first_check_delay_seconds=60
        )
        monitor.start()
        monitor.check_soon()
        for _ in range(100):
            if monitor.health:
                break
            await asyncio.sleep(0.01)
        await asyncio.wait_for(monitor.stop(), timeout=1)

    asyncio.run(scenario())
    assert checked == ["buzz.example.com"]


def test_dev_mode_has_no_monitor(make_app):
    app = make_app(domain="buzz.example.com", custom_domain_origin_host="traefik", dev_mode=True)

    assert app.state.certificate_monitor is None


def app_with_certificate_problem(make_app, reason):
    app = make_app(domain="buzz.example.com", custom_domain_origin_host="traefik", dev_mode=True)
    app.state.certificate_monitor = WildcardCertificateMonitor("traefik", "buzz.example.com")
    app.state.certificate_monitor.health = CertificateHealth(
        False, reason, "self-signed certificate", NOW + timedelta(days=5), NOW
    )
    return app


def dashboard_html(app):
    client = TestClient(app, base_url="https://buzz.example.com")
    client.cookies.set(session_cookie_name(False), DEV_SESSION_ID)
    return client.get("/").text


def certificate_warning_classes(html):
    return re.search(r'<p class="([^"]*)"[^>]*id="certificate-warning"', html).group(1).split()


@pytest.fixture
def degraded_app(make_app):
    return app_with_certificate_problem(make_app, "certificate_expiring")


def test_health_reports_degraded_certificate_without_failing(degraded_app):
    response = TestClient(degraded_app, base_url="https://buzz.example.com").get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["wildcard_certificate"]["reason"] == "certificate_expiring"


def test_dashboard_shows_certificate_warning(degraded_app):
    client = TestClient(degraded_app, base_url="https://buzz.example.com")
    client.cookies.set(session_cookie_name(False), DEV_SESSION_ID)

    response = client.get("/")

    assert response.status_code == 200
    assert 'id="certificate-warning"' in response.text
    assert "The certificate for *.buzz.example.com expires on" in response.text


@pytest.mark.parametrize(
    ("reason", "colour"),
    [
        ("certificate_invalid", "text-danger-dark"),
        ("certificate_expiring", "text-attention"),
        ("proxy_unreachable", "text-attention"),
    ],
)
def test_certificate_warning_colour_matches_its_severity(make_app, reason, colour):
    html = dashboard_html(app_with_certificate_problem(make_app, reason))

    assert colour in certificate_warning_classes(html)

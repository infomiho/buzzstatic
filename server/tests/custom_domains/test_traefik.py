import json
import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from server.custom_domains.traefik import (
    REVISION_HEADER,
    REVISION_HOST,
    REVISION_ROUTER,
    TraefikFileControl,
    dynamic_config,
    probe_config_revision,
    revision_max_age,
)
from server.traefik_file_provider import DynamicConfigError, TraefikDynamicFile

ROUTER = {
    "entryPoints": ["https"],
    "rule": "Host(`www.example.com`)",
    "service": "buzz@docker",
    "tls": {"certResolver": "letsencrypt"},
}


class FakeProbe:
    def __init__(self):
        self.revision = None
        self.error = None

    def __call__(self, origin_host):
        if self.error:
            raise self.error
        return self.revision


def make_control(tmp_path, probe=None):
    return TraefikFileControl(
        TraefikDynamicFile(tmp_path, "buzz-routes"),
        "coolify-proxy",
        "https",
        "buzz@docker",
        probe=probe or FakeProbe(),
    )


def written(tmp_path):
    return yaml.safe_load((tmp_path / "buzz-routes.yaml").read_text())


def served_revision(tmp_path):
    middleware = written(tmp_path)["http"]["middlewares"][REVISION_ROUTER]
    return middleware["headers"]["customResponseHeaders"][REVISION_HEADER]


def test_publish_writes_routers_and_revision_router(tmp_path):
    control = make_control(tmp_path)

    control.publish({"buzz-domain-1-g1": ROUTER})

    routers = written(tmp_path)["http"]["routers"]
    assert routers["buzz-domain-1-g1"] == ROUTER
    assert routers[REVISION_ROUTER] == {
        "entryPoints": ["https"],
        "middlewares": [REVISION_ROUTER],
        "rule": f"Host(`{REVISION_HOST}`)",
        "service": "buzz@docker",
        "tls": {},
    }


def test_dynamic_config_omits_empty_sections():
    assert dynamic_config({}) == {}


@pytest.mark.parametrize("name", [REVISION_ROUTER, "Upper", "bad name", "x" * 200])
def test_publish_rejects_unsafe_router_names(tmp_path, name):
    with pytest.raises(DynamicConfigError):
        make_control(tmp_path).publish({name: ROUTER})
    assert not (tmp_path / "buzz-routes.yaml").exists()


def test_revision_follows_content(tmp_path):
    control = make_control(tmp_path)
    control.publish({})
    empty = served_revision(tmp_path)
    control.publish({"buzz-domain-1-g1": ROUTER})
    with_router = served_revision(tmp_path)
    control.publish({})

    assert empty != with_router
    assert served_revision(tmp_path) == empty


def test_routers_count_as_loaded_only_when_traefik_serves_latest_revision(tmp_path):
    probe = FakeProbe()
    control = make_control(tmp_path, probe)
    control.publish({"buzz-domain-1-g1": ROUTER})
    previous = served_revision(tmp_path)
    control.publish({})

    probe.revision = previous
    control.refresh()
    assert control.loaded_routers() is None
    assert control.is_ready() is False

    probe.revision = served_revision(tmp_path)
    control.refresh()
    assert control.loaded_routers() == frozenset()
    assert control.is_ready() is True


def test_unreachable_proxy_is_not_loaded(tmp_path):
    probe = FakeProbe()
    control = make_control(tmp_path, probe)
    control.publish({"buzz-domain-1-g1": ROUTER})
    probe.revision = served_revision(tmp_path)
    control.refresh()
    probe.error = ConnectionRefusedError()

    control.refresh()

    assert control.loaded_routers() is None


def test_readiness_expires_without_fresh_load(tmp_path, monkeypatch):
    probe = FakeProbe()
    control = make_control(tmp_path, probe)
    control.publish({})
    probe.revision = served_revision(tmp_path)
    control.refresh()
    later = datetime.now(timezone.utc) + timedelta(minutes=1)
    monkeypatch.setattr(
        "server.custom_domains.traefik.datetime",
        type("Clock", (), {"now": staticmethod(lambda tz=None: later)}),
    )

    assert control.is_ready() is False


def test_write_failure_makes_control_unready(tmp_path):
    probe = FakeProbe()
    control = make_control(tmp_path, probe)
    control.publish({})
    probe.revision = served_revision(tmp_path)
    control.refresh()
    (tmp_path / "buzz-routes.yaml").unlink()
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(DynamicConfigError):
            control.publish({"buzz-domain-1-g1": ROUTER})
    finally:
        tmp_path.chmod(0o700)
    control.refresh()

    assert control.is_ready() is False
    assert control.loaded_routers() == frozenset()


@pytest.fixture
def tls_server(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TRAEFIK DEFAULT CERT")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    (tmp_path / "cert.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    listener = socket.create_server(("127.0.0.1", 0))
    server_names = []
    requests = []
    context.sni_callback = lambda _tls, server_name, _context: server_names.append(server_name)

    def serve():
        connection, _ = listener.accept()
        with context.wrap_socket(connection, server_side=True) as tls:
            requests.append(tls.recv(4096).decode())
            tls.sendall(
                b"HTTP/1.1 421 Misdirected Request\r\n"
                + f"{REVISION_HEADER}: abc123\r\n".encode()
                + b"Content-Length: 0\r\n\r\n"
            )

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield listener.getsockname()[1], server_names, requests
    listener.close()


def test_probe_reads_revision_through_tls_with_revision_sni(tls_server, monkeypatch):
    port, server_names, requests = tls_server
    real_create_connection = socket.create_connection
    monkeypatch.setattr(
        "server.custom_domains.traefik.socket.create_connection",
        lambda address, timeout: real_create_connection(("127.0.0.1", port), timeout),
    )

    assert probe_config_revision("coolify-proxy") == "abc123"
    [raw_request] = requests
    assert server_names == [REVISION_HOST]
    assert raw_request.startswith("HEAD / HTTP/1.1\r\n")
    assert f"Host: {REVISION_HOST}\r\n" in raw_request


def insert_routed_claim(database, **columns):
    values = {
        "hostname": "www.example.com",
        "site_name": "my-site",
        "verification_token": "bdv_test",
        "status": "verified",
        "created_at": "2026-07-16T00:00:00+00:00",
        "expires_at": "2099-07-17T00:00:00+00:00",
        "route_status": "routed",
        "route_generation": 1,
        **columns,
    }
    with database.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO sites (name) VALUES ('my-site')")
        conn.execute(
            f"""INSERT INTO custom_domain_claims ({", ".join(values)})
            VALUES ({", ".join("?" for _ in values)})""",
            tuple(values.values()),
        )


def route_status(database, hostname="www.example.com"):
    with database.connect() as conn:
        row = conn.execute(
            "SELECT route_status FROM custom_domain_claims WHERE hostname = ?", (hostname,)
        ).fetchone()
    return row["route_status"]


def wait_for(condition, attempts=300):
    for _ in range(attempts):
        if condition():
            return True
        threading.Event().wait(0.01)
    return False


def test_disabled_custom_domains_do_not_write_routing_config(make_app, tmp_path):
    app = make_app(custom_domains_enabled=False, traefik_dynamic_dir=tmp_path)

    with TestClient(app) as client:
        response = client.get("/health", headers={"host": "localhost:8080"})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert app.state.custom_domains.control is None
    assert not (tmp_path / "buzz-routes.yaml").exists()


def test_disabling_without_dynamic_dir_reports_unmanaged_routers(make_app, database, caplog):
    insert_routed_claim(database)

    with TestClient(make_app(custom_domains_enabled=False, traefik_dynamic_dir=None)) as client:
        health = client.get("/health", headers={"host": "localhost:8080"}).json()

    assert health["status"] == "degraded"
    assert health["custom_domains_error"] == "routers_unmanaged"
    assert "still route these hostnames to Buzz: www.example.com" in caplog.text


def test_disabling_withdraws_routers_and_then_reports_disabled(
    make_app, database, tmp_path, monkeypatch
):
    insert_routed_claim(database)
    routes_file = tmp_path / "buzz-routes.yaml"

    def traefik_serves_written_revision(_host):
        if not routes_file.exists():
            return None
        middleware = json.loads(routes_file.read_text())["http"]["middlewares"]
        return middleware[REVISION_ROUTER]["headers"]["customResponseHeaders"][REVISION_HEADER]

    monkeypatch.setattr(
        "server.custom_domains.traefik.probe_config_revision", traefik_serves_written_revision
    )
    app = make_app(
        custom_domains_enabled=False,
        traefik_dynamic_dir=tmp_path,
        custom_domain_reconcile_seconds=0.01,
    )

    with TestClient(app):
        assert wait_for(lambda: route_status(database) == "not_routed")
        assert wait_for(lambda: not app.state.custom_domains.withdrawing)
        capabilities = app.state.custom_domains.capabilities()

    routers = json.loads(routes_file.read_text())["http"]["routers"]
    assert set(routers) == {REVISION_ROUTER}
    assert capabilities.status == "disabled"
    assert capabilities.detail == "Custom domains are not enabled on this Buzz server"


def test_withdrawal_waits_for_traefik_to_load_the_routes_file(
    make_app, database, tmp_path, monkeypatch
):
    insert_routed_claim(database)
    monkeypatch.setattr(
        "server.custom_domains.traefik.probe_config_revision", lambda _host: None
    )
    app = make_app(
        custom_domains_enabled=False,
        traefik_dynamic_dir=tmp_path,
        custom_domain_reconcile_seconds=0.01,
    )

    with TestClient(app):
        assert wait_for(lambda: (tmp_path / "buzz-routes.yaml").exists())
        threading.Event().wait(0.1)
        capabilities = app.state.custom_domains.capabilities()

    assert route_status(database) == "removing"
    assert capabilities.detail == (
        "Custom domains are disabled and their Traefik routers are being withdrawn"
    )


def test_active_cloudflare_claim_without_runtime_keeps_canonical_hosting(
    make_app, database, caplog
):
    insert_routed_claim(
        database, claim_mode="cloudflare", activated_at="2026-07-16T00:00:00+00:00"
    )
    app = make_app(custom_domains_enabled=True, traefik_dynamic_dir=None)

    with TestClient(app) as client:
        health = client.get("/health", headers={"host": "localhost:8080"}).json()
        custom_host = client.get("/", headers={"host": "www.example.com"})

    assert health["status"] == "degraded"
    assert health["custom_domains_error"] == "routers_unmanaged"
    assert custom_host.status_code == 421
    assert "Set BUZZ_TRAEFIK_DYNAMIC_DIR" in caplog.text


def test_enabled_custom_domains_publish_routing_config(make_app, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "server.custom_domains.traefik.probe_config_revision", lambda _host: None
    )
    config_path = tmp_path / "buzz-routes.yaml"
    app = make_app(
        custom_domains_enabled=True,
        traefik_dynamic_dir=tmp_path,
        custom_domain_reconcile_seconds=0.01,
    )

    with TestClient(app):
        for _ in range(200):
            if config_path.exists():
                break
            threading.Event().wait(0.01)

    routers = json.loads(config_path.read_text())["http"]["routers"]
    assert set(routers) == {REVISION_ROUTER}


@pytest.mark.parametrize(("token", "expected"), [(None, False), ("operator-secret", True)])
def test_operator_listener_starts_only_with_operator_token(
    make_app, tmp_path, monkeypatch, token, expected
):
    events = []

    class FakeOperatorServer:
        def __init__(self, operator_token, port, handoff_provider, cancel_provider):
            assert (operator_token, port) == ("operator-secret", 8081)
            events.append("created")

        def start(self):
            events.append("started")

        def stop(self):
            events.append("stopped")

    monkeypatch.setattr("server.custom_domains.runtime.OperatorServer", FakeOperatorServer)
    monkeypatch.setattr(
        "server.custom_domains.traefik.probe_config_revision", lambda _host: None
    )
    app = make_app(
        custom_domains_enabled=True,
        traefik_dynamic_dir=tmp_path,
        custom_domain_operator_token=token,
    )

    with TestClient(app):
        pass

    assert events == (["created", "started", "stopped"] if expected else [])


def test_lifespan_runs_transition_detection_before_legacy_validators(
    make_app, tmp_path, monkeypatch
):
    events = []
    observed = threading.Event()

    class Loop:
        def __init__(self, name):
            self.name = name

        def run_once(self):
            events.append(self.name)
            if self.name == "cloudflare":
                observed.set()

    monkeypatch.setattr("server.custom_domains.runtime.DomainRouteReconciler", lambda *a, **k: Loop("route"))
    monkeypatch.setattr("server.custom_domains.runtime.DomainActivator", lambda *a, **k: Loop("direct"))
    monkeypatch.setattr(
        "server.custom_domains.runtime.CloudflareDiagnostician", lambda *a, **k: Loop("cloudflare")
    )
    monkeypatch.setattr(
        "server.custom_domains.runtime.DomainTransitionCoordinator", lambda *a, **k: Loop("transition")
    )
    app = make_app(custom_domains_enabled=True, traefik_dynamic_dir=tmp_path)

    with TestClient(app):
        assert observed.wait(2)

    assert events.index("route") < events.index("transition")
    assert events.index("transition") < events.index("direct")
    assert events.index("transition") < events.index("cloudflare")


def test_revision_acknowledgements_outlive_a_slow_reconcile_interval():
    assert revision_max_age(5) == timedelta(seconds=30)
    assert revision_max_age(60) == timedelta(seconds=180)

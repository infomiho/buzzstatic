from fastapi.testclient import TestClient

from server.custom_domains.claims import DomainClaimStore
from server.custom_domains.transitions import DomainClaimStateMachine

DOMAIN = "buzz.example.com"
ONE_YEAR = "max-age=31536000"
ONE_DAY = "max-age=86400"


def activate_custom_domain(database, site_name, hostname):
    with database.connect() as conn:
        conn.execute("INSERT INTO sites (name) VALUES (?)", (site_name,))
        store = DomainClaimStore(conn)
        claim = store.create(site_name, hostname)
        store.record_check(claim.id, site_name, (claim.verification_value,))
        claim = store.prepare_routes(True)[0]
        store.mark_routed(claim.id, claim.route_generation)
        claim = store.get(claim.id, site_name)
        DomainClaimStateMachine(conn).apply_activation_decision(claim, None)


def hsts(client, host):
    response = client.get("/", headers={"host": host})
    return response.headers.get("strict-transport-security")


def test_control_host_and_sites_require_https_for_a_year(make_app):
    client = TestClient(make_app(domain=DOMAIN))

    assert hsts(client, DOMAIN) == ONE_YEAR
    assert hsts(client, f"my-site.{DOMAIN}") == ONE_YEAR


def test_activated_custom_domain_requires_https_for_a_day(make_app, database):
    activate_custom_domain(database, "my-site", "www.example.com")
    client = TestClient(make_app(domain=DOMAIN, custom_domains_enabled=True))

    assert hsts(client, "www.example.com") == ONE_DAY


def test_unknown_host_gets_no_policy(make_app):
    client = TestClient(make_app(domain=DOMAIN))

    response = client.get("/", headers={"host": "unknown.example.org"})

    assert response.status_code == 421
    assert "strict-transport-security" not in response.headers


def test_local_server_without_tls_sends_no_policy(make_app):
    client = TestClient(make_app())

    assert hsts(client, "localhost:8080") is None
    assert hsts(client, "my-site.localhost:8080") is None

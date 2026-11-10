import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.https_redirect import (
    HttpsRedirect,
    https_redirect_config,
    probe_https_redirect,
)
from server.traefik_file_provider import TraefikDynamicFile, serialize

DOMAIN = "buzz.example.com"


def published_config(directory: Path) -> dict:
    return json.loads((directory / "buzz-http.yaml").read_text())


def test_redirect_router_covers_the_domain_and_its_site_subdomains():
    config = https_redirect_config(DOMAIN, "web")

    assert config["http"]["routers"] == {
        "buzz-http": {
            "entryPoints": ["web"],
            "rule": "Host(`buzz.example.com`) || HostRegexp(`.+\\.buzz\\.example\\.com`)",
            "middlewares": ["buzz-redirect-to-https"],
            "service": "noop@internal",
        }
    }
    assert config["http"]["middlewares"] == {
        "buzz-redirect-to-https": {"redirectScheme": {"scheme": "https", "permanent": True}}
    }


def test_redirect_config_is_a_valid_dynamic_file():
    assert serialize(https_redirect_config(DOMAIN, "http"))


def test_publish_writes_the_redirect_once(tmp_path):
    redirect = HttpsRedirect(TraefikDynamicFile(tmp_path, "buzz-http"), DOMAIN, "http")

    assert redirect.publish() is True
    first_inode = (tmp_path / "buzz-http.yaml").stat().st_ino
    assert redirect.publish() is True

    assert published_config(tmp_path) == https_redirect_config(DOMAIN, "http")
    assert (tmp_path / "buzz-http.yaml").stat().st_ino == first_inode


def test_publish_reports_a_failed_write(tmp_path):
    missing_directory = tmp_path / "missing"
    redirect = HttpsRedirect(TraefikDynamicFile(missing_directory, "buzz-http"), DOMAIN, "http")

    assert redirect.publish() is False


def test_server_publishes_the_redirect_at_startup(make_app, tmp_path):
    dynamic_dir = tmp_path / "dynamic"
    dynamic_dir.mkdir()
    app = make_app(
        domain=DOMAIN,
        traefik_dynamic_dir=dynamic_dir,
        traefik_http_entrypoint="web",
        custom_domain_origin_host=None,
    )

    with TestClient(app):
        pass

    assert published_config(dynamic_dir) == https_redirect_config(DOMAIN, "web")


def test_server_without_a_dynamic_directory_publishes_nothing(make_settings):
    assert HttpsRedirect.from_settings(make_settings(domain=DOMAIN)) is None


def test_dev_server_publishes_nothing(make_settings, tmp_path):
    settings = make_settings(domain=DOMAIN, traefik_dynamic_dir=tmp_path, dev_mode=True)

    assert HttpsRedirect.from_settings(settings) is None


@pytest.fixture
def proxy(monkeypatch):
    """A local stand-in for the proxy's port 80 that answers every request the same way."""
    servers = []

    def _start(status, location=None):
        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        monkeypatch.setattr("server.https_redirect.PROXY_HTTP_PORT", server.server_port)
        return "127.0.0.1"

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_probe_accepts_a_redirect_to_https(proxy):
    proxy_host = proxy(301, f"https://{DOMAIN}/")

    assert probe_https_redirect(proxy_host, DOMAIN) is None


@pytest.mark.parametrize(
    ("status", "location"),
    [(503, None), (404, None), (301, f"http://{DOMAIN}/elsewhere")],
)
def test_probe_reports_a_proxy_that_does_not_redirect_to_https(proxy, status, location):
    proxy_host = proxy(status, location)

    assert probe_https_redirect(proxy_host, DOMAIN) == "not_redirecting"


def test_probe_reports_an_unreachable_proxy(monkeypatch):
    closed_port = 9
    monkeypatch.setattr("server.https_redirect.PROXY_HTTP_PORT", closed_port)

    assert probe_https_redirect("127.0.0.1", DOMAIN) == "proxy_unreachable"


def run_until_checked(redirect: HttpsRedirect, checks: list) -> None:
    async def scenario():
        redirect.start()
        for _ in range(100):
            if checks:
                break
            await asyncio.sleep(0.01)
        await asyncio.wait_for(redirect.stop(), timeout=1)

    asyncio.run(scenario())


def test_running_redirect_reports_what_the_probe_finds(tmp_path):
    checks = []

    def probe(proxy_host, domain):
        checks.append((proxy_host, domain))
        return "not_redirecting"

    redirect = HttpsRedirect(
        TraefikDynamicFile(tmp_path, "buzz-http"),
        DOMAIN,
        "http",
        proxy_host="traefik",
        probe=probe,
        settle_seconds=0,
    )

    run_until_checked(redirect, checks)

    assert checks == [("traefik", DOMAIN)]
    assert redirect.error_code == "not_redirecting"


def test_running_redirect_reports_a_failed_publish_without_probing(tmp_path):
    checks = []
    redirect = HttpsRedirect(
        TraefikDynamicFile(tmp_path / "missing", "buzz-http"),
        DOMAIN,
        "http",
        proxy_host="traefik",
        probe=lambda proxy_host, domain: checks.append(domain),
        settle_seconds=0,
    )

    async def scenario():
        redirect.start()
        for _ in range(100):
            if redirect.error_code:
                break
            await asyncio.sleep(0.01)
        await asyncio.wait_for(redirect.stop(), timeout=1)

    asyncio.run(scenario())

    assert redirect.error_code == "publish_failed"
    assert checks == []


def test_health_is_degraded_while_plain_http_does_not_redirect(make_app, tmp_path):
    app = make_app(
        domain=DOMAIN,
        traefik_dynamic_dir=tmp_path,
        cloudflare_dns_api_token="token",
        custom_domain_origin_host=None,
    )
    client = TestClient(app, base_url=f"https://{DOMAIN}")
    assert client.get("/health").json()["status"] == "ok"
    app.state.https_redirect.error_code = "not_redirecting"

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["http_redirect_error"] == "not_redirecting"


def test_health_reports_no_redirect_error_without_a_dynamic_directory(make_app):
    app = make_app(domain=DOMAIN, custom_domain_origin_host=None)

    response = TestClient(app, base_url=f"https://{DOMAIN}").get("/health")

    assert response.json()["http_redirect_error"] is None

import json
from pathlib import Path

from fastapi.testclient import TestClient

from server.https_redirect import HttpsRedirect, https_redirect_config
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

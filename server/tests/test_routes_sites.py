import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from server.app import DeploymentBodyLimitMiddleware, create_app
from server.auth_service import AuthService
from server.exceptions import BadRequest
from server.routes.sites import build_site_url, validate_site_name
from server.site_store import SiteRecord, SiteStore


def _archive(files: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return output.getvalue()


class TestValidateSiteName:
    def test_translates_invalid_subdomain_to_bad_request(self):
        with pytest.raises(BadRequest):
            validate_site_name("")


class TestBuildSiteUrl:
    def test_with_domain(self):
        assert build_site_url("my-site", "example.com", 8080) == "https://my-site.example.com"

    def test_without_domain_custom_port(self):
        assert build_site_url("my-site", None, 3000) == "http://my-site.localhost:3000"


def test_deploy_returns_explicit_site_name(make_app, monkeypatch):
    monkeypatch.setattr(
        "server.routes.sites._deploy_site",
        lambda database,
        settings,
        site_name,
        archive,
        owner_id,
        source,
        actor,
        credential,
        configure,
        content_roots: SiteRecord(
            name=site_name,
            owner_id=owner_id,
            size_bytes=0,
            last_deployed_at="2026-07-16T00:00:00+00:00",
            deployment_number=1,
        ),
    )
    client = TestClient(make_app(dev_mode=True))

    response = client.post(
        "/deploy",
        headers={
            "host": "localhost:8080",
            "origin": "http://localhost:8080",
            "x-buzz-site": "my-site",
        },
        files={"file": ("site.zip", b"zip", "application/zip")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "name": "my-site",
        "site_name": "my-site",
        "url": "http://my-site.localhost:8080",
        "private": False,
        "deployment_number": 1,
    }


@pytest.mark.parametrize(
    ("header", "expected"),
    [({"x-buzz-access": "private"}, True), ({}, False)],
)
def test_deploy_passes_requested_visibility(make_app, monkeypatch, header, expected):
    captured = {}

    def deploy_stub(
        database,
        settings,
        site_name,
        archive,
        owner_id,
        source,
        actor,
        credential,
        configure,
        content_roots,
    ):
        captured["configure"] = configure
        return SiteRecord(site_name, owner_id, 0, "2026-07-16T00:00:00+00:00", 1)

    monkeypatch.setattr("server.routes.sites._deploy_site", deploy_stub)
    client = TestClient(make_app(dev_mode=True))

    response = client.post(
        "/deploy",
        headers={"x-buzz-site": "private-site", **header},
        files={"file": ("site.zip", b"zip", "application/zip")},
    )

    assert response.status_code == 200
    assert (captured["configure"] is not None) is expected


def test_deploy_records_dashboard_user_provenance(make_app, monkeypatch):
    captured = {}

    def deploy_stub(
        database,
        settings,
        site_name,
        archive,
        owner_id,
        source,
        actor,
        credential,
        configure,
        content_roots,
    ):
        captured.update(source=source, actor=actor, credential=credential)
        return SiteRecord(site_name, owner_id, 0, "2026-07-16T00:00:00+00:00", 1)

    monkeypatch.setattr("server.routes.sites._deploy_site", deploy_stub)
    client = TestClient(make_app(dev_mode=True))
    client.cookies.set("buzz_session", "dev")

    response = client.post(
        "/deploy",
        headers={
            "host": "localhost:8080",
            "origin": "http://localhost:8080",
            "x-buzz-site": "my-site",
        },
        files={"file": ("site.zip", b"zip", "application/zip")},
    )

    assert response.status_code == 200
    assert captured == {
        "source": "dashboard",
        "actor": "dev",
        "credential": None,
    }


def test_deploy_records_named_token_as_actor(make_app, database, monkeypatch):
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO users (id, github_id, github_login) VALUES (1, 1, 'alice')"
        )
        conn.execute(
            "INSERT INTO sites (name, owner_id) VALUES ('my-site', 1)"
        )
    token = AuthService(database.connect).create_deploy_token(
        1, "my-site", "Production CI"
    ).raw_token
    captured = {}

    def deploy_stub(
        database,
        settings,
        site_name,
        archive,
        owner_id,
        source,
        actor,
        credential,
        configure,
        content_roots,
    ):
        captured.update(source=source, actor=actor, credential=credential)
        return SiteRecord(site_name, owner_id, 0, "2026-07-16T00:00:00+00:00", 1)

    monkeypatch.setattr("server.routes.sites._deploy_site", deploy_stub)
    response = TestClient(make_app()).post(
        "/deploy",
        headers={
            "authorization": f"Bearer {token}",
            "x-buzz-site": "my-site",
        },
        files={"file": ("site.zip", b"zip", "application/zip")},
    )

    assert response.status_code == 200
    assert captured == {
        "source": "api",
        "actor": "alice",
        "credential": "Production CI",
    }


def test_deployment_keeps_actor_after_token_is_deleted(make_app, database, tmp_path):
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO users (id, github_id, github_login) VALUES (1, 1, 'alice')"
        )
        conn.execute("INSERT INTO sites (name, owner_id) VALUES ('my-site', 1)")
    auth = AuthService(database.connect)
    token = auth.create_deploy_token(1, "my-site", "Production CI")
    client = TestClient(make_app())

    deployed = client.post(
        "/deploy",
        headers={
            "authorization": f"Bearer {token.raw_token}",
            "x-buzz-site": "my-site",
        },
        files={"file": ("site.zip", _archive({"index.html": "hello"}), "application/zip")},
    )
    auth.delete_deploy_token(1, token.id_prefix)
    session = auth.login_by_user_id(1).token
    listed = client.get(
        "/sites/my-site/deployments",
        headers={"authorization": f"Bearer {session}"},
    )

    assert deployed.status_code == 200
    assert listed.status_code == 200
    assert listed.json()[0]["actor"] == "alice"
    assert listed.json()[0]["credential"] == "Production CI"


def test_deploy_rejects_an_unknown_access_header(make_app):
    client = TestClient(make_app(dev_mode=True))

    response = client.post(
        "/deploy",
        headers={"x-buzz-site": "private-site", "x-buzz-access": "public"},
        files={"file": ("site.zip", b"zip", "application/zip")},
    )

    assert response.status_code == 400
    assert "X-Buzz-Access must be 'private'" in response.json()["detail"]


def test_deploy_rejects_compressed_upload_over_limit(make_app):
    client = TestClient(make_app(dev_mode=True, max_archive_bytes=4))

    response = client.post(
        "/deploy",
        files={"file": ("site.zip", b"12345", "application/zip")},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "ZIP exceeds the 4-byte compressed upload limit"}


def test_deploy_rejects_request_body_before_multipart_parsing(make_app):
    app = make_app(dev_mode=True)
    app.add_middleware(DeploymentBodyLimitMiddleware, max_body_bytes=100)
    client = TestClient(app)

    response = client.post(
        "/deploy",
        files={"file": ("site.zip", b"12345", "application/zip")},
    )

    assert response.status_code == 413
    assert response.json() == {
        "detail": "Request body exceeds the configured deployment limit"
    }


def test_deploy_rejects_chunked_request_body_over_limit(make_app):
    app = make_app(dev_mode=True)
    app.add_middleware(DeploymentBodyLimitMiddleware, max_body_bytes=100)
    client = TestClient(app)
    body = (
        b"--buzz\r\n"
        b'Content-Disposition: form-data; name="file"; filename="site.zip"\r\n'
        b"Content-Type: application/zip\r\n\r\n"
        + b"a" * 120
        + b"\r\n--buzz--\r\n"
    )

    response = client.post(
        "/deploy",
        content=iter((body[:80], body[80:])),
        headers={"content-type": "multipart/form-data; boundary=buzz"},
    )

    assert response.status_code == 413
    assert response.json() == {
        "detail": "Request body exceeds the configured deployment limit"
    }


def test_deploy_authenticates_before_parsing_multipart():
    client = TestClient(create_app())

    response = client.post(
        "/deploy",
        content=b"not multipart",
        headers={"content-type": "multipart/form-data; boundary=missing"},
    )

    assert response.status_code == 401


def test_lists_and_activates_site_deployments(make_app, database, tmp_path):
    with database.connect() as conn:
        store = SiteStore(
            conn, tmp_path, deployments_dir=tmp_path / ".deployments"
        )
        store.deploy("my-site", io.BytesIO(_archive({"index.html": "one"})), 1)
        store.deploy("my-site", io.BytesIO(_archive({"index.html": "two"})), 1)
    app = make_app(dev_mode=True)
    client = TestClient(app)

    listed = client.get("/sites/my-site/deployments")
    activated = client.post("/sites/my-site/deployments/1/activate")

    assert listed.status_code == 200
    assert [item["deployment_number"] for item in listed.json()] == [2, 1]
    assert listed.json()[0]["source"] == "api"
    assert listed.json()[0]["actor"] == "API"
    assert listed.json()[0]["credential"] is None
    assert "file_count" not in listed.json()[0]
    assert activated.status_code == 200
    assert activated.json()["deployment_number"] == 1
    assert client.get("/", headers={"host": "my-site.localhost:8080"}).text == "one"


def test_deploy_rejects_the_retired_subdomain_header(make_app):
    """Ignoring it would fall through to a generated name, silently publishing
    to a new random site while the intended one went untouched."""
    client = TestClient(make_app(dev_mode=True))

    response = client.post(
        "/deploy",
        headers={"x-subdomain": "my-site"},
        files={"file": ("site.zip", b"zip", "application/zip")},
    )

    assert response.status_code == 400
    assert "X-Buzz-Site" in response.json()["detail"]

import hashlib
import secrets
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from server.cookies import COOKIE_NAME

SERVER_URL = "http://localhost:8080"


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def signed_in_client(app, database):
    token = "buzz_sess_" + secrets.token_urlsafe(32)
    with database.connect() as conn:
        user_id = conn.execute(
            "INSERT INTO users (github_id, github_login, github_name) VALUES (?, ?, ?)",
            (42, "alice", "Alice"),
        ).lastrowid
        conn.execute(
            "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                user_id,
                (datetime.now() + timedelta(days=1)).isoformat(),
            ),
        )
    client = TestClient(app, base_url=SERVER_URL, headers={"Origin": SERVER_URL})
    client.cookies.set(COOKIE_NAME, token)
    return client


def test_unauthenticated_visitor_signs_in_and_returns_to_approval(app):
    response = TestClient(app).get("/device", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/?next=/device"


def test_approval_form_names_server_and_account(signed_in_client):
    page = signed_in_client.get("/device").text

    assert SERVER_URL in page
    assert "@alice" in page
    assert "Approve sign-in as @alice" in page
    assert '<label for="user_code"' in page
    assert "Use another account" in page
    assert f"{SERVER_URL}/device" in page


def test_invalid_code_suggests_login_on_this_server(signed_in_client):
    page = signed_in_client.post("/device", data={"user_code": "NOPE-NOPE"}).text

    assert 'role="alert"' in page
    assert f"buzz login --server {SERVER_URL}" in page
    assert 'aria-invalid="true"' in page


def test_success_names_server_and_account(app, signed_in_client):
    grant = app.state.device_authorization.start()

    page = signed_in_client.post("/device", data={"user_code": grant.user_code}).text

    assert "Device connected" in page
    assert SERVER_URL in page
    assert "@alice" in page

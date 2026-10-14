import json
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from server.custom_domains.errors import ClaimConflict, ClaimNotFound
from server.custom_domains.operator import OperatorServer


def request(server, path, token="operator-secret", method="GET"):
    return urlopen(
        Request(
            f"http://127.0.0.1:{server.port}{path}",
            headers={"Authorization": f"Bearer {token}"},
            method=method,
        )
    )


@contextmanager
def running(handoffs=lambda: [], cancel=lambda claim_id: {}, token="operator-secret"):
    server = OperatorServer(token, 0, handoffs, cancel, host="127.0.0.1")
    server.start()
    try:
        yield server
    finally:
        server.stop()


def test_empty_operator_token_is_rejected():
    with pytest.raises(ValueError):
        OperatorServer(" , ", 0, lambda: [], lambda claim_id: {}, host="127.0.0.1")


def test_transition_list_accepts_every_rotated_token():
    handoffs = [{"claim_id": 7, "hostname": "www.example.com"}]

    with running(lambda: handoffs, token="old-operator-secret, operator-secret") as server:
        for token in ("old-operator-secret", "operator-secret"):
            with request(server, "/operator/domain-transitions?source=test", token) as response:
                assert json.loads(response.read()) == {"transitions": handoffs}
                assert response.headers["Cache-Control"] == "no-store"
        with pytest.raises(HTTPError) as error:
            request(server, "/operator/domain-transitions", "wrong")
        assert error.value.code == 401


def test_operator_routes_authenticate_before_route_and_method_handling():
    with running() as server:
        with pytest.raises(HTTPError) as missing:
            request(server, "/operator/missing", "wrong")
        assert missing.value.code == 401
        assert missing.value.headers["WWW-Authenticate"] == "Bearer"
        assert missing.value.headers["Cache-Control"] == "no-store"
        assert json.loads(missing.value.read()) == {"detail": "Unauthorized"}
        with pytest.raises(HTTPError) as method:
            request(server, "/operator/domain-transitions", "wrong", "POST")
        assert method.value.code == 401
        with pytest.raises(HTTPError) as authorized_method:
            request(server, "/operator/domain-transitions", method="POST")
        assert authorized_method.value.code == 405
        assert authorized_method.value.headers["Allow"] == "GET"
        assert authorized_method.value.headers["Cache-Control"] == "no-store"
        with pytest.raises(HTTPError) as cancel_method:
            request(server, "/operator/domain-transitions/7/cancel", method="PUT")
        assert cancel_method.value.code == 405
        assert cancel_method.value.headers["Allow"] == "POST"
        with pytest.raises(HTTPError) as unknown:
            request(server, "/traefik")
        assert unknown.value.code == 404


def test_operator_provider_failure_is_no_store_json():
    def fail():
        raise RuntimeError("provider failed")

    with running(fail) as server:
        with pytest.raises(HTTPError) as error:
            request(server, "/operator/domain-transitions")
        assert error.value.code == 500
        assert error.value.headers["Content-Type"] == "application/json"
        assert error.value.headers["Cache-Control"] == "no-store"
        assert json.loads(error.value.read())["detail"] == "Could not read transitions"


def test_transition_cancellation_maps_results_and_errors():
    actions = []

    def cancel(claim_id):
        actions.append(claim_id)
        if claim_id == 8:
            raise ClaimConflict("Transition changed")
        if claim_id == 9:
            raise ClaimNotFound("Custom domain claim not found")
        return {"claim_id": claim_id, "state": "cancelled"}

    with running(cancel=cancel) as server:
        with request(server, "/operator/domain-transitions/7/cancel", method="POST") as response:
            assert response.status == 200
            assert json.load(response) == {"claim_id": 7, "state": "cancelled"}
        for claim_id, status in ((8, 409), (9, 404)):
            with pytest.raises(HTTPError) as error:
                request(server, f"/operator/domain-transitions/{claim_id}/cancel", method="POST")
            assert error.value.code == status
            assert json.loads(error.value.read())["detail"]
            error.value.close()
        for raw_claim_id in ("0", "07", "-1", "x"):
            with pytest.raises(HTTPError) as error:
                request(
                    server, f"/operator/domain-transitions/{raw_claim_id}/cancel", method="POST"
                )
            assert error.value.code == 404
        assert actions == [7, 8, 9]

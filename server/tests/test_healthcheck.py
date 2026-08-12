from server import healthcheck


class HealthyResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_healthcheck_uses_control_domain(monkeypatch):
    monkeypatch.setenv("BUZZ_DOMAIN", "buzz.example.com")
    request_details = {}

    def open_request(request, timeout):
        request_details["host"] = request.get_header("Host")
        request_details["timeout"] = timeout
        request_details["url"] = request.full_url
        return HealthyResponse()

    monkeypatch.setattr(healthcheck, "urlopen", open_request)

    healthcheck.main()

    assert request_details == {
        "host": "buzz.example.com",
        "timeout": 3,
        "url": "http://127.0.0.1:8080/health",
    }


def test_healthcheck_defaults_to_local_control_host(monkeypatch):
    monkeypatch.delenv("BUZZ_DOMAIN", raising=False)
    hosts = []
    monkeypatch.setattr(
        healthcheck,
        "urlopen",
        lambda request, timeout: hosts.append(request.get_header("Host"))
        or HealthyResponse(),
    )

    healthcheck.main()

    assert hosts == ["localhost:8080"]

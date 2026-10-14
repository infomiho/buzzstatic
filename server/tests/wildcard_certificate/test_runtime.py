import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from server.wildcard_certificate import WildcardCertificateRuntime
from server.wildcard_certificate.manager import CertificateStatus
from server.wildcard_certificate.runtime import WildcardCertificateConfig, next_check_delay

COMPLETE = {
    "domain": "buzz.example.com",
    "traefik_dynamic_dir": Path("/traefik-dynamic"),
    "cloudflare_dns_api_token": "token",
}


def test_config_is_built_from_complete_settings(make_settings):
    config = WildcardCertificateConfig.from_settings(make_settings(**COMPLETE))

    assert config.domain == "buzz.example.com"
    assert config.dynamic_dir == Path("/traefik-dynamic")
    assert config.acme_directory_url == "https://acme-v02.api.letsencrypt.org/directory"


@pytest.mark.parametrize(
    "override",
    [
        {"domain": None},
        {"traefik_dynamic_dir": None},
        {"cloudflare_dns_api_token": None},
        {"dev_mode": True},
    ],
)
def test_runtime_is_disabled_unless_fully_configured(make_settings, override):
    settings = make_settings(**{**COMPLETE, **override})

    assert WildcardCertificateConfig.from_settings(settings) is None
    assert not WildcardCertificateRuntime.from_settings(settings).enabled


@pytest.mark.parametrize("override", [{"domain": None}, {"traefik_dynamic_dir": None}, {"dev_mode": True}])
def test_runtime_without_a_publishing_target_reports_nothing(make_settings, override):
    settings = make_settings(**{**COMPLETE, **override, "cloudflare_dns_api_token": None})

    assert WildcardCertificateRuntime.from_settings(settings).error_code is None


def test_missing_token_is_reported_when_buzz_should_own_the_certificate(make_settings, caplog):
    settings = make_settings(**{**COMPLETE, "cloudflare_dns_api_token": None})
    runtime = WildcardCertificateRuntime.from_settings(settings)

    runtime.start()

    assert runtime.error_code == "not_configured"
    assert "BUZZ_CLOUDFLARE_DNS_API_TOKEN is not set" in caplog.text


def test_default_app_has_a_disabled_runtime(client):
    assert not client.app.state.wildcard_certificate.enabled


def test_health_reports_only_the_error_code(client):
    client.app.state.wildcard_certificate.status = CertificateStatus(
        None, False, False, "CloudflareDnsError: Cloudflare GET /zones failed", "issuance_failed"
    )

    response = client.get("/health", headers={"host": "localhost:8080"})

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["wildcard_certificate_error"] == "issuance_failed"
    assert "Cloudflare" not in response.text


def test_failures_are_retried_sooner_than_healthy_checks():
    delays = [next_check_delay(failures) for failures in range(6)]

    assert delays == [
        timedelta(minutes=5),
        timedelta(minutes=2),
        timedelta(minutes=5),
        timedelta(minutes=15),
        timedelta(minutes=30),
        timedelta(minutes=30),
    ]


class Manager:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    def ensure(self):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_check_once_records_status():
    status = CertificateStatus(None, True, True, None)
    runtime = WildcardCertificateRuntime(Manager(status))

    assert asyncio.run(runtime.check_once()) is status
    assert runtime.status is status


def test_unexpected_failure_becomes_an_error_status():
    runtime = WildcardCertificateRuntime(Manager(PermissionError("dynamic dir")))

    status = asyncio.run(runtime.check_once())

    assert (status.published, status.error, status.error_code) == (
        False,
        "dynamic dir",
        "check_failed",
    )


def test_consecutive_failures_are_counted_until_a_check_succeeds():
    manager = Manager(CertificateStatus(None, False, False, "down", "issuance_failed"))
    runtime = WildcardCertificateRuntime(manager)

    asyncio.run(runtime.check_once())
    asyncio.run(runtime.check_once())
    failures = runtime._consecutive_failures
    manager.outcome = CertificateStatus(None, True, True, None)
    asyncio.run(runtime.check_once())

    assert (failures, runtime._consecutive_failures) == (2, 0)


def test_publishing_asks_for_a_prompt_health_check():
    published = []
    manager = Manager(CertificateStatus(None, False, True, None))
    runtime = WildcardCertificateRuntime(manager, on_published=lambda: published.append(1))

    asyncio.run(runtime.check_once())
    asyncio.run(runtime.check_once())
    manager.outcome = CertificateStatus(None, True, True, None)
    asyncio.run(runtime.check_once())

    assert len(published) == 2


def test_loop_checks_on_start_and_stops_promptly():
    manager = Manager(CertificateStatus(None, False, True, None))

    async def scenario():
        runtime = WildcardCertificateRuntime(manager)
        runtime.start()
        await asyncio.sleep(0.05)
        await asyncio.wait_for(runtime.stop(), timeout=1)

    asyncio.run(scenario())
    assert manager.calls == 1

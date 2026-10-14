import json
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from server.traefik_file_provider import TraefikDynamicFile
from server.wildcard_certificate.manager import WildcardCertificateManager
from tests.wildcard_certificate.fake_ca import NOW, FakeCa

DOMAINS = ["buzz.example.com", "*.buzz.example.com"]


class Issuer:
    def __init__(self):
        self.ca = FakeCa()
        self.calls = 0
        self.lifetime = timedelta(days=90)
        self.domains = None
        self.failure = None
        self.wrong_key = False

    def __call__(self, domains, key):
        self.calls += 1
        if self.failure:
            raise self.failure
        public_key = ec.generate_private_key(ec.SECP256R1()).public_key() if self.wrong_key else key.public_key()
        return self.ca.issue(public_key, self.domains or domains, NOW, self.lifetime)


@pytest.fixture
def issuer():
    return Issuer()


@pytest.fixture
def dynamic_dir(tmp_path):
    path = tmp_path / "dynamic"
    path.mkdir()
    return path


def make_manager(
    tmp_path, dynamic_dir, issuer, now=NOW, domain="buzz.example.com", directory_url=None
):
    return WildcardCertificateManager(
        domain,
        tmp_path / "tls" / "wildcard-certificate.json",
        TraefikDynamicFile(dynamic_dir, "buzz-certificate"),
        issuer,
        directory_url=directory_url,
        clock=lambda: now,
    )


def published_chain(dynamic_dir):
    document = json.loads((dynamic_dir / "buzz-certificate.yaml").read_text())
    return document["tls"]["certificates"][0]["certFile"]


def test_first_check_issues_stores_and_publishes(tmp_path, dynamic_dir, issuer):
    status = make_manager(tmp_path, dynamic_dir, issuer).ensure()

    assert (status.renewed, status.published, status.error) == (True, True, None)
    assert status.not_after == NOW + timedelta(days=90)
    state_file = tmp_path / "tls" / "wildcard-certificate.json"
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    assert published_chain(dynamic_dir) == json.loads(state_file.read_text())["chain"]
    document = json.loads((dynamic_dir / "buzz-certificate.yaml").read_text())
    assert list(document) == ["tls"]
    assert "stores" not in document["tls"]


def test_fresh_certificate_is_not_reissued(tmp_path, dynamic_dir, issuer):
    make_manager(tmp_path, dynamic_dir, issuer).ensure()

    status = make_manager(tmp_path, dynamic_dir, issuer, NOW + timedelta(days=59)).ensure()

    assert issuer.calls == 1
    assert (status.renewed, status.published) == (False, True)


def test_deleted_dynamic_file_is_republished_without_reissuing(
    tmp_path, dynamic_dir, issuer, caplog
):
    make_manager(tmp_path, dynamic_dir, issuer).ensure()
    chain = published_chain(dynamic_dir)
    (dynamic_dir / "buzz-certificate.yaml").unlink()

    make_manager(tmp_path, dynamic_dir, issuer).ensure()

    assert issuer.calls == 1
    assert published_chain(dynamic_dir) == chain
    assert "buzz-certificate.yaml was missing or changed, so Buzz wrote it again" in caplog.text


def test_renews_in_the_last_third_of_the_lifetime(tmp_path, dynamic_dir, issuer):
    make_manager(tmp_path, dynamic_dir, issuer).ensure()
    first_chain = published_chain(dynamic_dir)

    status = make_manager(tmp_path, dynamic_dir, issuer, NOW + timedelta(days=61)).ensure()

    assert issuer.calls == 2
    assert status.renewed
    assert published_chain(dynamic_dir) != first_chain


def test_short_lived_certificates_renew_proportionally(tmp_path, dynamic_dir, issuer):
    issuer.lifetime = timedelta(days=6)
    make_manager(tmp_path, dynamic_dir, issuer).ensure()

    make_manager(tmp_path, dynamic_dir, issuer, NOW + timedelta(days=3)).ensure()
    assert issuer.calls == 1
    make_manager(tmp_path, dynamic_dir, issuer, NOW + timedelta(days=4, hours=1)).ensure()
    assert issuer.calls == 2


def test_changed_domain_triggers_a_new_certificate(tmp_path, dynamic_dir, issuer):
    make_manager(tmp_path, dynamic_dir, issuer).ensure()

    status = make_manager(tmp_path, dynamic_dir, issuer, domain="static.example.org").ensure()

    assert status.renewed
    assert issuer.calls == 2


def test_failed_renewal_keeps_publishing_the_current_certificate(tmp_path, dynamic_dir, issuer):
    make_manager(tmp_path, dynamic_dir, issuer).ensure()
    chain = published_chain(dynamic_dir)
    issuer.failure = OSError("Cloudflare API unreachable")

    status = make_manager(tmp_path, dynamic_dir, issuer, NOW + timedelta(days=70)).ensure()

    assert (status.renewed, status.published) == (False, True)
    assert "Cloudflare API unreachable" in status.error
    assert status.not_after == NOW + timedelta(days=90)
    assert published_chain(dynamic_dir) == chain


def test_first_issuance_failure_publishes_nothing(tmp_path, dynamic_dir, issuer):
    issuer.failure = RuntimeError("rate limited")

    status = make_manager(tmp_path, dynamic_dir, issuer).ensure()

    assert (status.not_after, status.published) == (None, False)
    assert status.error_code == "issuance_failed"
    assert list(dynamic_dir.iterdir()) == []


@pytest.mark.parametrize("defect", ["wrong_names", "wrong_key", "expired"])
def test_unusable_certificate_from_the_ca_is_rejected(tmp_path, dynamic_dir, issuer, defect):
    if defect == "wrong_names":
        issuer.domains = ["buzz.example.com"]
    elif defect == "wrong_key":
        issuer.wrong_key = True
    now = NOW + timedelta(days=91) if defect == "expired" else NOW

    status = make_manager(tmp_path, dynamic_dir, issuer, now).ensure()

    assert status.error and status.error.startswith("CertificateRejected")
    assert not (tmp_path / "tls" / "wildcard-certificate.json").exists()
    assert list(dynamic_dir.iterdir()) == []


def test_unwritable_dynamic_dir_is_reported_after_storing(tmp_path, issuer):
    status = make_manager(tmp_path, tmp_path / "not-mounted", issuer).ensure()

    assert (status.renewed, status.published) == (True, False)
    assert "Cannot write" in status.error
    assert status.error_code == "publish_failed"
    assert (tmp_path / "tls" / "wildcard-certificate.json").exists()


def test_corrupt_state_file_is_replaced(tmp_path, dynamic_dir, issuer):
    state = tmp_path / "tls" / "wildcard-certificate.json"
    state.parent.mkdir()
    state.write_text("{not json")

    status = make_manager(tmp_path, dynamic_dir, issuer).ensure()

    assert status.renewed and status.published


def test_domain_case_does_not_cause_a_new_order(tmp_path, dynamic_dir, issuer):
    status = make_manager(tmp_path, dynamic_dir, issuer, domain="Buzz.Example.com").ensure()
    make_manager(tmp_path, dynamic_dir, issuer, domain="Buzz.Example.com").ensure()

    assert (status.renewed, status.published, status.error) == (True, True, None)
    assert issuer.calls == 1


def test_issued_certificate_survives_a_failed_save_without_a_new_order(
    tmp_path, dynamic_dir, issuer, monkeypatch
):
    manager = make_manager(tmp_path, dynamic_dir, issuer)
    save = manager._save

    def full_disk(certificate):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(manager, "_save", full_disk)
    failed = manager.ensure()
    monkeypatch.setattr(manager, "_save", save)
    recovered = manager.ensure()

    assert (failed.renewed, failed.published, failed.error_code) == (True, True, "storage_failed")
    assert (recovered.renewed, recovered.error) == (False, None)
    assert issuer.calls == 1
    assert manager.stored() is not None


def test_switching_acme_directory_orders_a_new_certificate(tmp_path, dynamic_dir, issuer):
    make_manager(tmp_path, dynamic_dir, issuer, directory_url="https://staging.test/dir").ensure()

    status = make_manager(
        tmp_path, dynamic_dir, issuer, directory_url="https://production.test/dir"
    ).ensure()

    assert status.renewed
    assert issuer.calls == 2
    state = json.loads((tmp_path / "tls" / "wildcard-certificate.json").read_text())
    assert state["directory_url"] == "https://production.test/dir"


def test_state_without_a_directory_is_kept(tmp_path, dynamic_dir, issuer):
    make_manager(tmp_path, dynamic_dir, issuer).ensure()

    status = make_manager(
        tmp_path, dynamic_dir, issuer, directory_url="https://production.test/dir"
    ).ensure()

    assert not status.renewed
    assert issuer.calls == 1


def test_lock_file_is_private(tmp_path, dynamic_dir, issuer):
    make_manager(tmp_path, dynamic_dir, issuer).ensure()

    lock = tmp_path / "tls" / "wildcard-certificate.lock"
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600


def test_concurrent_checks_place_one_order(tmp_path, dynamic_dir, issuer):
    issue = issuer.__call__

    def slow_issue(domains, key):
        time.sleep(0.2)
        return issue(domains, key)

    managers = [make_manager(tmp_path, dynamic_dir, slow_issue) for _ in range(2)]
    with ThreadPoolExecutor(2) as pool:
        statuses = list(pool.map(lambda manager: manager.ensure(), managers))

    assert issuer.calls == 1
    assert sorted(status.renewed for status in statuses) == [False, True]

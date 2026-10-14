import pytest

from server.main import access_control_warning, domain_error


def test_warns_when_nobody_can_log_in():
    warning = access_control_warning(False, None, 0)
    assert warning is not None
    assert "Nobody can log in" in warning


@pytest.mark.parametrize(
    ("registration_open", "allowlist", "user_count"),
    [
        (True, None, 0),
        (False, frozenset({"alice"}), 0),
        (False, None, 2),
    ],
)
def test_no_warning_when_somebody_can_log_in(registration_open, allowlist, user_count):
    assert access_control_warning(registration_open, allowlist, user_count) is None


@pytest.mark.parametrize("domain", ["buzz.example.com", "Buzz.Example.COM", "buzz.localhost"])
def test_bare_hostname_is_accepted(domain):
    assert domain_error(domain) is None


@pytest.mark.parametrize("domain", [None, ""])
def test_missing_domain_is_rejected(domain):
    assert "BUZZ_DOMAIN is not set" in domain_error(domain)


@pytest.mark.parametrize(
    "domain",
    [
        "https://buzz.example.com",
        "buzz.example.com/",
        "buzz.example.com:8443",
        "buzz.example.com.",
        " buzz.example.com",
        "*.buzz.example.com",
        "-buzz.example.com",
    ],
)
def test_domain_that_is_not_a_bare_hostname_is_rejected(domain):
    assert "is not a bare hostname" in domain_error(domain)

import pytest

from server.environment import (
    ENVIRONMENT_BY_NAME,
    InvalidEnvironment,
    ENVIRONMENT_VARIABLES,
    parse_bool,
    parse_github_logins,
    parse_positive_int,
    parse_public_ips,
)


def test_environment_variable_names_are_unique():
    names = [variable.name for variable in ENVIRONMENT_VARIABLES]
    assert len(names) == len(set(names))


def test_environment_registry_covers_server_and_deployment_settings():
    assert set(ENVIRONMENT_BY_NAME) == {
        "BUZZ_PORT",
        "BUZZ_DATA_DIR",
        "BUZZ_DOMAIN",
        "GITHUB_CLIENT_ID",
        "GITHUB_CLIENT_SECRET",
        "BUZZ_ANALYTICS_SECRET",
        "BUZZ_MAX_ARCHIVE_BYTES",
        "BUZZ_MAX_SITE_BYTES",
        "BUZZ_MAX_SITE_FILES",
        "BUZZ_MAX_ARCHIVE_PATH_BYTES",
        "BUZZ_ALLOW_REGISTRATION",
        "BUZZ_ALLOWED_GITHUB_USERS",
        "BUZZ_TRAEFIK_DYNAMIC_DIR",
        "BUZZ_CLOUDFLARE_DNS_API_TOKEN",
        "BUZZ_ACME_DIRECTORY_URL",
        "BUZZ_CUSTOM_DOMAINS_ENABLED",
        "BUZZ_TRAEFIK_HTTPS_ENTRYPOINT",
        "BUZZ_TRAEFIK_SERVICE",
        "BUZZ_CUSTOM_DOMAIN_OPERATOR_TOKEN",
        "BUZZ_MAX_CUSTOM_DOMAINS_PER_SITE",
        "BUZZ_MAX_CUSTOM_DOMAINS_PER_USER",
        "BUZZ_MAX_CUSTOM_DOMAINS_SERVER_WIDE",
        "BUZZ_CUSTOM_DOMAIN_INGRESS_IPS",
        "BUZZ_CUSTOM_DOMAIN_ORIGIN_HOST",
        "BUZZ_TRAEFIK_CERT_RESOLVER",
        "BUZZ_CUSTOM_DOMAIN_RECONCILE_SECONDS",
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("BUZZ_MAX_SITE_FILES", "10k"),
        ("BUZZ_ALLOW_REGISTRATION", "maybe"),
        ("BUZZ_CUSTOM_DOMAIN_INGRESS_IPS", "10.0.0.1"),
        ("BUZZ_CUSTOM_DOMAIN_RECONCILE_SECONDS", "0"),
    ],
)
def test_invalid_value_names_the_variable(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(InvalidEnvironment, match=f"^Invalid {name}: "):
        ENVIRONMENT_BY_NAME[name].read()


def test_custom_domain_certificates_default_to_production_resolver(monkeypatch):
    monkeypatch.delenv("BUZZ_TRAEFIK_CERT_RESOLVER", raising=False)
    assert ENVIRONMENT_BY_NAME["BUZZ_TRAEFIK_CERT_RESOLVER"].read() == "letsencrypt"


@pytest.mark.parametrize(
    "name", ["BUZZ_ALLOW_REGISTRATION", "BUZZ_MAX_SITE_FILES", "BUZZ_CUSTOM_DOMAINS_ENABLED"]
)
def test_empty_value_reads_as_default(monkeypatch, name):
    monkeypatch.setenv(name, "")
    variable = ENVIRONMENT_BY_NAME[name]
    assert variable.read() == variable.default


@pytest.mark.parametrize("value", ["1", "true", "True", " yes ", "on"])
def test_parse_bool_truthy(value):
    assert parse_bool(value) is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", " no ", "off"])
def test_parse_bool_falsy(value):
    assert parse_bool(value) is False


def test_parse_bool_rejects_unknown_value():
    with pytest.raises(ValueError):
        parse_bool("maybe")


def test_parse_positive_int_rejects_zero_and_negative_values():
    assert parse_positive_int("5") == 5
    with pytest.raises(ValueError, match="positive integer"):
        parse_positive_int("0")
    with pytest.raises(ValueError, match="positive integer"):
        parse_positive_int("-1")


def test_parse_github_logins_normalizes_and_skips_blanks():
    assert parse_github_logins(" Alice , BOB ,,") == frozenset({"alice", "bob"})
    assert parse_github_logins("") == frozenset()


def test_allowed_github_users_defaults_to_not_set(monkeypatch):
    monkeypatch.delenv("BUZZ_ALLOWED_GITHUB_USERS", raising=False)
    assert ENVIRONMENT_BY_NAME["BUZZ_ALLOWED_GITHUB_USERS"].read() is None


def test_allow_registration_defaults_to_true(monkeypatch):
    monkeypatch.delenv("BUZZ_ALLOW_REGISTRATION", raising=False)
    assert ENVIRONMENT_BY_NAME["BUZZ_ALLOW_REGISTRATION"].read() is True


def test_custom_domains_default_to_disabled(monkeypatch):
    monkeypatch.delenv("BUZZ_CUSTOM_DOMAINS_ENABLED", raising=False)
    assert ENVIRONMENT_BY_NAME["BUZZ_CUSTOM_DOMAINS_ENABLED"].read() is False


def test_public_ingress_ips_are_normalized_and_must_be_global():
    assert parse_public_ips(" 8.8.8.8,2001:4860:4860::8888 ") == frozenset(
        {"8.8.8.8", "2001:4860:4860::8888"}
    )
    with pytest.raises(ValueError, match="public ingress"):
        parse_public_ips("127.0.0.1")


def test_sensitive_settings_are_marked():
    sensitive = {
        variable.name for variable in ENVIRONMENT_VARIABLES if variable.sensitive
    }
    assert sensitive == {
        "GITHUB_CLIENT_SECRET",
        "BUZZ_ANALYTICS_SECRET",
        "BUZZ_CUSTOM_DOMAIN_OPERATOR_TOKEN",
        "BUZZ_CLOUDFLARE_DNS_API_TOKEN",
    }
